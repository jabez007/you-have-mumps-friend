# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
from pathlib import Path as FilePath

import pytest
from pydantic import ValidationError, field_validator, model_validator

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    NativeStore,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import (
    MutationError,
    NotFound,
    ReferenceViolation,
    SchemaError,
    StorageError,
    TransactionError,
)
from tests.adversarial_support import dump
from tests.deletion_models import cycle_declaration, declarations
from tests.test_native_schema_safety import ready
from tests.test_partial_mutations import ObservedStore

ROOTS = (
    "^YHMFTestProduct",
    "^YHMFTestWarehouse",
    "^YHMFTestOrder",
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
)


@pytest.fixture
def graph(store):
    target, source, detail, line = declarations()
    db = Database(store, Registry(target, source))
    db.insert(target(id="T"))
    return db, target, source, detail, line


def test_nested_set_null_and_cascade_preserve_unrelated_owners_and_indexes(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    db.insert(
        source(
            id="S",
            optional=ref,
            detail=detail(link=ref),
            lines={"A": line(id="A", link=ref), "B": line(id="B", detail=detail(link=ref))},
        )
    )
    assert db.delete(target, "T")
    value = db.get(source, "S")
    assert value.optional is None and value.detail.link is None
    assert list(value.lines) == ["B"] and value.lines["B"].detail.link is None
    assert db.count(source, "S", collection=("lines",)) == 1
    assert db.check_indexes(source).issues == ()
    assert db.query(source).where(optional=None).all()[0].id == "S"
    assert db.store.get(Path("^YHMFRef", (b"YHMFTestProduct", b"s:T"))) is None
    assert not db.delete(target, "T")


def test_delete_policy_schema_golden(graph):
    db, target, source, detail, line = graph
    fixture = json.loads(
        (FilePath(__file__).with_name("fixtures") / "delete-policies-v1.json").read_text()
    )
    assert db.registry.spec(source).snapshot == canonical_json(fixture)


def test_policy_only_migration_requires_review_and_preserves_records_and_edges(store):
    target, source, detail, line = declarations()

    class Before(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        link: Reference[target] | None = Field(default=None, index=True)

    class After(Before):
        link: Reference[target] | None = Field(default=None, index=True, on_delete="set_null")

    old = Database(store, Registry(target, Before))
    old.insert(target(id="T"))
    old.insert(Before(id="S", link=Reference[target](key="T")))
    previous = dump(store, ("^YHMFRef", "^YHMFIndex", "^YHMFUnique"))
    path = old.registry.spec(Before).path("S").child(b"link")
    raw = store.get(path)
    db = Database(store, Registry(target, After))
    plan = db.plan_migration(After)
    assert plan.execution_supported and plan.validation_only and plan.requires_manual_review
    assert [change.kind for change in plan.changes] == ["delete_policy_changed"]
    status = db.begin_migration(Before, After, plan=plan)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete" and store.get(path) == raw
    assert dump(store, ("^YHMFRef", "^YHMFIndex", "^YHMFUnique")) == previous
    assert db.delete(target, "T") and db.get(After, "S").link is None


def test_root_cascade_dominates_children_and_internal_restrict_edges(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    db.insert(
        source(
            id="S", protected=ref, optional=ref, cascading=ref, lines={"A": line(id="A", link=ref)}
        )
    )
    assert db.delete(target, "T")
    with pytest.raises(NotFound):
        db.get(source, "S")
    assert db.check_indexes(source).issues == ()


def test_external_restrict_edge_rejects_the_complete_cascade_without_changes(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    db.insert(source(id="cascade", cascading=ref))
    db.insert(source(id="restrict", protected=ref, optional=ref))
    before = dump(db.store, ROOTS)
    with pytest.raises(ReferenceViolation):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_mixed_policy_cycles_are_decided_after_the_full_closure(store):
    node = cycle_declaration()
    db = Database(store, Registry(node))
    db.insert(node(id="A"))
    db.insert(node(id="B", cascading=Reference[node](key="A")))
    db.patch(
        node,
        "A",
        changes={"cascading": Reference[node](key="B"), "protected": Reference[node](key="B")},
    )
    assert db.delete(node, "A")
    assert db.scan(node) == [] and db.check_indexes(node).issues == ()


def test_set_null_cycle_preserves_the_surviving_record(store):
    node = cycle_declaration()
    db = Database(store, Registry(node))
    db.insert(node(id="A"))
    db.insert(node(id="B", optional=Reference[node](key="A")))
    db.patch(node, "A", changes={"optional": Reference[node](key="B")})
    assert db.delete(node, "A")
    assert db.get(node, "B").optional is None
    assert db.check_indexes(node).issues == ()


def test_combined_child_removals_check_final_collection_length(graph):
    db, target, source, detail, line = graph

    class Required(source):
        lines: Children[line] = ChildField(key="id", min_length=1)

    db = Database(db.store, Registry(target, Required))
    ref = Reference[target](key="T")
    db.insert(Required(id="S", lines={k: line(id=k, link=ref) for k in ("A", "B")}))
    before = dump(db.store, ROOTS)
    with pytest.raises(MutationError, match="length"):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before
    db.patch(Required, "S", changes={"cascading": ref})
    assert db.delete(target, "T")
    with pytest.raises(NotFound):
        db.get(Required, "S")


def test_set_null_requires_a_nullable_reference_declaration(graph):
    db, target, source, detail, line = graph

    class Invalid(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        link: Reference[target] = Field(on_delete="set_null")

    with pytest.raises(SchemaError, match="nullable"):
        Registry(target, Invalid)


def test_set_null_runs_custom_validation_and_rolls_back(store):
    target, source, detail, line = declarations()

    class Guarded(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        link: Reference[target] | None = Field(default=None, on_delete="set_null")

        @field_validator("link")
        @classmethod
        def require_reference(cls, value):
            if value is None:
                raise ValueError("must retain the reference")
            return value

    db = Database(store, Registry(target, Guarded))
    db.insert(target(id="T"))
    db.insert(Guarded(id="S", link=Reference[target](key="T")))
    before = dump(store, ROOTS)
    with pytest.raises(ValidationError):
        db.delete(target, "T")
    assert dump(store, ROOTS) == before


def test_cascade_child_does_not_bypass_custom_ancestor_validators(graph):
    db, target, source, detail, line = graph

    class Guarded(source):
        @model_validator(mode="after")
        def inspect_children(self):
            return self

    db = Database(db.store, Registry(target, Guarded))
    db.insert(Guarded(id="S", lines={"A": line(id="A", link=Reference[target](key="T"))}))
    before = dump(db.store, ROOTS)
    with pytest.raises(MutationError, match="custom validators"):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_unregistered_sources_fail_instead_of_guessing_their_policies(graph):
    db, target, source, detail, line = graph
    db.insert(source(id="S", optional=Reference[target](key="T")))
    limited = Database(db.store, Registry(target))
    before = dump(db.store, ROOTS)
    with pytest.raises(ReferenceViolation, match="register|Register"):
        limited.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_changed_registered_sources_keep_the_declaration_guard_diagnostic(graph):
    db, target, source, detail, line = graph
    db.insert(source(id="S", optional=Reference[target](key="T")))
    source.__schema_version__ = 2
    before = dump(db.store, ROOTS)
    with pytest.raises(SchemaError, match="changed"):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_null_validation_cannot_retain_a_dangling_target(store):
    target, source, detail, line = declarations()

    class Retaining(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        link: Reference[target] | None = Field(default=None, on_delete="set_null")

        @field_validator("link")
        @classmethod
        def retain(cls, value):
            return value if value is not None else Reference[target](key="T")

    db = Database(store, Registry(target, Retaining))
    db.insert(target(id="T"))
    db.insert(Retaining(id="S"))
    before = dump(store, ROOTS)
    with pytest.raises(ReferenceViolation):
        db.delete(target, "T")
    assert dump(store, ROOTS) == before


def test_count_and_actual_reverse_edges_must_agree(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    db.insert(source(id="S", optional=ref, cascading=ref))
    db.store.set(Path("^YHMFRef", (b"YHMFTestProduct", b"s:T")), b"1")
    before = dump(db.store, ROOTS)
    with pytest.raises(StorageError, match="count|budget"):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_delete_owner_bound_is_checked_before_any_writes(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    for i in range(101):
        db.insert(source(id=str(i), cascading=ref))
    observed = ObservedStore(db.store)
    db = Database(observed, db.registry)
    with pytest.raises(MutationError, match="owner"):
        db.delete(target, "T")
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)


def test_delete_incoming_edge_bound_is_checked_before_decoding_sources(graph):
    db, target, source, detail, line = graph
    ref = Reference[target](key="T")
    db.insert(
        source(
            id="S", cascading=ref, lines={str(i): line(id=str(i), link=ref) for i in range(1000)}
        )
    )
    observed = ObservedStore(db.store)
    db = Database(observed, db.registry)
    with pytest.raises(MutationError, match="edge"):
        db.delete(target, "T")
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)


def test_delete_decoded_node_bound_is_checked_before_writes(graph):
    db, target, source, detail, line = graph
    for key in ("A", "B"):
        db.insert(
            source(
                id=key,
                cascading=Reference[target](key="T"),
                lines={str(i): line(id=str(i)) for i in range(1000)},
            )
        )
    observed = ObservedStore(db.store)
    db = Database(observed, db.registry)
    with pytest.raises(MutationError, match="node"):
        db.delete(target, "T")
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)


def test_failure_after_secondary_updates_rolls_back_the_entire_delete(graph, monkeypatch):
    db, target, source, detail, line = graph
    db.insert(source(id="S", optional=Reference[target](key="T")))
    before = dump(db.store, ROOTS)
    real = db.store.delete_tree
    root = db.registry.spec(target).path("T")

    def fail(path):
        if path == root:
            raise RuntimeError("injected physical delete failure")
        real(path)

    monkeypatch.setattr(db.store, "delete_tree", fail)
    with pytest.raises(RuntimeError, match="injected"):
        db.delete(target, "T")
    assert dump(db.store, ROOTS) == before


def test_caught_delete_failure_poisoning_rolls_back_prior_changes(graph):
    db, target, source, detail, line = graph
    db.insert(source(id="S", protected=Reference[target](key="T")))
    before = dump(db.store, ROOTS)

    def attempted(tx):
        tx.insert(target(id="other"))
        try:
            tx.delete(target, "T")
        except ReferenceViolation:
            pass

    with pytest.raises(TransactionError):
        db.atomic(attempted)
    assert dump(db.store, ROOTS) == before


@pytest.mark.native
def test_native_policy_delete_restart_reloads_sources_and_counts(graph):
    db, target, source, detail, line = graph
    if not isinstance(db.store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")
    ref = Reference[target](key="T")
    db.insert(source(id="S", optional=ref, lines={"A": line(id="A", link=ref)}))
    attempts = []

    def remove(tx):
        attempts.append(tx.get(source, "S").optional)
        tx.delete(target, "T")
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(remove)
    assert len(attempts) == 2 and attempts[0] == attempts[1] == ref
    assert db.get(source, "S").optional is None and db.get(source, "S").lines == {}
    assert db.check_indexes(source).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("other", ["link", "delete_source", "unlink_child", "delete_target"])
@pytest.mark.parametrize("delete_first", [True, False])
def test_native_policy_delete_serializes_with_other_processes(graph, other, delete_first):
    db, target, source, detail, line = graph
    if not isinstance(db.store, NativeStore):
        pytest.skip("Requires native concurrency")
    ref = Reference[target](key="T")
    db.insert(
        source(
            id="S",
            optional=ref if other == "delete_source" else None,
            lines={"A": line(id="A", link=ref)} if other == "unlink_child" else {},
        )
    )
    commands = ("delete_target", other) if delete_first else (other, "delete_target")
    results = _policy_race(commands)
    second = "committed"
    if other == "delete_target":
        second = "absent"
    elif delete_first and other in {"link", "unlink_child"}:
        second = "ReferenceViolation" if other == "link" else "NotFound"
    assert results == ["committed", second]
    with pytest.raises(NotFound):
        db.get(target, "T")
    if other == "delete_source":
        with pytest.raises(NotFound):
            db.get(source, "S")
    else:
        value = db.get(source, "S")
        assert value.optional is None
        if other == "unlink_child":
            assert list(value.lines) == ([] if delete_first else ["A"])
    assert db.check_indexes(source).issues == ()


def _policy_race(commands):
    workers = []
    try:
        for operation in commands:
            workers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(FilePath(__file__).with_name("native_delete_policies.py")),
                        operation,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for worker in workers:
            ready(worker)
        results = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0, error
            results.append(output.strip())
        return results
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
