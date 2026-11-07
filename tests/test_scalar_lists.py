# SPDX-License-Identifier: GPL-3.0-or-later
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Literal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError, create_model, model_validator

from mccann_hub.yhmf import (
    Database,
    Field,
    MemoryStore,
    Model,
    NativeStore,
    Patch,
    Reference,
    Registry,
    Transform,
)
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import SchemaError, StorageError, UniqueViolation
from tests.scalar_list_models import BudgetChild, BudgetRoot, ListRecord, State
from tests.test_native_schema_safety import ready
from tests.test_partial_mutations import ObservedStore


def test_scalar_list_golden_and_missing_empty_null_semantics(store):
    db = Database(store, Registry(ListRecord))
    record = ListRecord(
        id="A", values=[1, None, -2], labels=["", "雪", "a\x00b"], states=[State.OPEN]
    )
    db.insert(record)
    assert db.get(ListRecord, "A") == record
    fixture = json.loads(
        (FilePath(__file__).with_name("fixtures") / "scalar-list-v1.json").read_text()
    )
    spec = db.registry.spec(ListRecord)
    assert spec.snapshot == canonical_json(fixture["schema"])
    for path, value in fixture["nodes"]:
        assert store.get(spec.path("A").child(*(part.encode() for part in path))) == value.encode()
    db.patch(ListRecord, "A", changes={"values": []})
    assert db.get(ListRecord, "A").values == []
    db.patch(ListRecord, "A", changes={"values": None})
    assert db.get(ListRecord, "A").values is None
    store.delete_tree(spec.path("A").child(b"values"))
    with pytest.raises(StorageError, match="missing"):
        db.get(ListRecord, "A")


@pytest.mark.parametrize(
    "annotation,value",
    [
        (str, ["", "雪", "a\x00b"]),
        (int, [0, -(10**80), 10**80]),
        (bool, [True, False]),
        (float, [0.0, -1.5]),
        (bytes, [b"", b"\x00\xff"]),
        (Decimal, [Decimal("0.00"), Decimal("12.50")]),
        (date, [date.min, date.max]),
        (datetime, [datetime(2026, 11, 6, tzinfo=UTC)]),
        (UUID, [UUID(int=0), UUID(int=2**128 - 1)]),
        (State, [State.OPEN, State.CLOSED]),
        (Literal["a", "b"], ["a", "b"]),
    ],
)
def test_each_supported_scalar_roundtrips_in_a_list(store, annotation, value):
    owner = create_model(
        "Owner",
        __base__=Model,
        id=(str, Field(primary_key=True)),
        values=(list[annotation], Field(index=True)),
    )
    owner.__global__ = "^YHMFTestProduct"
    db = Database(store, Registry(owner))
    db.insert(owner(id="A", values=value))
    assert db.get(owner, "A").values == value
    assert db.query(owner).where(values=value).all()[0].values == value


@given(st.lists(st.one_of(st.none(), st.integers(-1000, 1000)), max_size=10))
def test_generated_list_roundtrip_and_replacement(values):
    db = Database(MemoryStore(), Registry(ListRecord))
    db.insert(ListRecord(id="A", values=values))
    assert db.get(ListRecord, "A").values == values
    db.patch(ListRecord, "A", changes={"values": list(reversed(values))})
    assert db.get(ListRecord, "A").values == list(reversed(values))
    assert db.check_indexes(ListRecord).issues == ()


@pytest.mark.parametrize(
    "corrupt",
    [
        "length",
        "ordinal",
        "gap",
        "extra",
        "null_children",
        "item",
        "oversized",
        "null_item_children",
    ],
)
def test_corrupt_list_shapes_fail_safely(store, corrupt):
    db = Database(store, Registry(ListRecord))
    db.insert(ListRecord(id="A", values=[1, None]))
    path = db.registry.spec(ListRecord).path("A").child(b"values")
    if corrupt == "length":
        store.set(path, b"l:02")
    elif corrupt == "ordinal":
        store.delete_tree(path.child(b"1"))
        store.set(path.child(b"01"), b"i:1")
    elif corrupt == "gap":
        store.delete_tree(path.child(b"1"))
    elif corrupt == "extra":
        store.set(path.child(b"3"), b"i:3")
    elif corrupt == "null_children":
        store.set(path, b"n:")
    elif corrupt == "item":
        store.set(path.child(b"1"), b"not tagged")
    elif corrupt == "oversized":
        store.set(path, b"l:1001")
    else:
        store.set(path.child(b"2", b"extra"), b"s:unexpected")
    with pytest.raises(StorageError):
        db.get(ListRecord, "A")


def test_list_equality_indexes_preserve_order_duplicates_and_atomic_claims(store):
    db = Database(store, Registry(ListRecord))
    db.insert(ListRecord(id="A", values=[1, 2], labels=["x", "x"]))
    db.insert(ListRecord(id="B", values=[2, 1]))
    assert db.query(ListRecord).where(values=[1, 2]).all()[0].id == "A"
    occurrences = db.query(ListRecord).where(values=[1, 2]).occurrences()
    assert len(occurrences) == 1 and occurrences[0].record.values == [1, 2]
    assert db.query(ListRecord).where(labels=["x"]).all() == []
    with pytest.raises(UniqueViolation):
        db.patch(ListRecord, "A", changes={"values": [2, 1], "labels": ["changed"]})
    assert db.get(ListRecord, "A").labels == ["x", "x"]
    db.patch_many(
        [
            Patch(ListRecord, "A", changes={"values": [2, 1]}),
            Patch(ListRecord, "B", changes={"values": [1, 2]}),
        ]
    )
    assert db.query(ListRecord).where(values=[1, 2]).all()[0].id == "B"
    assert db.check_indexes(ListRecord).issues == ()


def test_list_patch_writes_only_changed_and_removed_ordinals(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(ListRecord))
    db.insert(ListRecord(id="A", values=[1, 2, 3], labels=["preserved"]))
    observed.operations.clear()
    db.patch(ListRecord, "A", changes={"values": [1, 9]})
    path = db.registry.spec(ListRecord).path("A").child(b"values")
    writes = [
        (op, p)
        for op, p in observed.operations
        if op in {"set", "delete"} and p.global_name == "^YHMFTestProduct"
    ]
    assert set(writes) == {
        ("set", path),
        ("set", path.child(b"2")),
        ("delete", path.child(b"3")),
    }
    assert db.get(ListRecord, "A").labels == ["preserved"]


def test_list_growth_and_child_insertion_share_exact_root_budget(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(BudgetRoot))
    db.insert(
        BudgetRoot(
            id="A",
            children={
                str(i): BudgetChild(id=str(i), values=[0] * (1000 if i < 9 else 964))
                for i in range(10)
            },
        )
    )
    observed.operations.clear()
    with pytest.raises(StorageError, match="node budget"):
        db.patch(BudgetRoot, "A", at=("children", "9"), changes={"values": [0] * 965})
    with pytest.raises(StorageError, match="node budget"):
        db.insert_child(BudgetRoot, "A", collection=("children",), record=BudgetChild(id="new"))
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)
    db.patch_many(
        [
            Patch(BudgetRoot, "A", at=("children", "0"), changes={"values": [0] * 999}),
            Patch(BudgetRoot, "A", at=("children", "9"), changes={"values": [0] * 965}),
        ]
    )
    assert len(db.get(BudgetRoot, "A", at=("children", "9")).values) == 965


def test_scalar_list_length_and_cross_field_validation_are_not_bypassed(store):
    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        values: list[int] = Field(max_length=2)
        maximum: int = 5

        @model_validator(mode="after")
        def check_total(self):
            if sum(self.values) > self.maximum:
                raise ValueError("total too large")
            return self

    db = Database(store, Registry(Owner))
    db.insert(Owner(id="A", values=[]))
    for value in ([1, 2, 3], [3, 3], [True]):
        with pytest.raises(ValidationError):
            db.patch(Owner, "A", changes={"values": value})
    assert db.get(Owner, "A").values == []


@pytest.mark.parametrize(
    "annotation",
    [
        list[list[int]],
        list[Reference[BudgetRoot]],
        list[BudgetChild],
    ],
)
def test_nested_or_unsupported_list_items_are_rejected(annotation):
    owner = create_model(
        "Owner", __base__=Model, id=(str, Field(primary_key=True)), values=(annotation, ...)
    )
    owner.__global__ = "^YHMFTestProduct"
    with pytest.raises(SchemaError, match="list|List"):
        Registry(owner)


def test_list_addition_uses_explicit_record_transform(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class After(Before):
        values: list[bytes] = Field(default_factory=list)

    Database(store, Registry(Before)).insert(Before(id="A"))
    db = Database(store, Registry(After))
    assert not db.plan_migration(After).execution_supported
    transform = Transform("list.bytes.v1", lambda old: After(id=old.id, values=[b"\x00\xff"]))
    plan = db.plan_migration(After, transform=transform)
    status = db.begin_migration(Before, After, plan=plan, transform=transform)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After, transform=transform)
    assert status.phase == "complete" and db.get(After, "A").values == [b"\x00\xff"]


def test_nullable_decimal_list_schema_supports_revision_migrations(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        values: list[Decimal | None] = Field(index=True)

    class After(Before):
        __schema_version__ = 2

    Database(store, Registry(Before)).insert(Before(id="A", values=[Decimal("1.00"), None]))
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.validation_only
    status = db.begin_migration(Before, After, plan=plan)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete"
    assert db.get(After, "A").values == [Decimal("1.00"), None]
    assert db.query(After).where(values=[Decimal("1"), None]).all()[0].id == "A"


def test_list_item_bound_rejects_persistence_and_query_before_native_key_construction(store):
    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        values: list[int] = Field(index=True)

    db = Database(store, Registry(Owner))
    with pytest.raises(StorageError, match="1000"):
        db.insert(Owner(id="A", values=[0] * 1001))
    with pytest.raises(StorageError, match="1000"):
        db.query(Owner).where(values=[0] * 1001).all()


@pytest.mark.native
@pytest.mark.parametrize("mode", ["separate_fields", "same_field", "unique", "delete"])
def test_native_stale_list_writers_preserve_fields_claims_and_deletions(store, mode):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native concurrency")
    db = Database(store, Registry(ListRecord))
    db.insert(ListRecord(id="A"))
    db.insert(ListRecord(id="B"))
    first = ("delete" if mode == "delete" else "patch", "A", "values", "[1]")
    second = (
        "patch",
        "B" if mode == "unique" else "A",
        "labels" if mode == "separate_fields" else "values",
        '["changed"]' if mode == "separate_fields" else "[1]" if mode == "unique" else "[2]",
    )
    workers = []
    try:
        for command in (first, second):
            workers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(FilePath(__file__).with_name("native_scalar_lists.py")),
                        *command,
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
        assert results == [
            "committed",
            "UniqueViolation"
            if mode == "unique"
            else "NotFound"
            if mode == "delete"
            else "committed",
        ]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    if mode == "delete":
        assert db.query(ListRecord).where(values=[2]).all() == []
    else:
        record = db.get(ListRecord, "A")
        assert record.values == ([2] if mode == "same_field" else [1])
        assert record.labels == (["changed"] if mode == "separate_fields" else [])
    assert db.check_indexes(ListRecord).issues == ()


@pytest.mark.native
def test_native_list_patch_restarts_without_partial_node_or_claim_updates(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native transaction restart")
    yottadb = pytest.importorskip("yottadb")
    db = Database(store, Registry(ListRecord))
    db.insert(ListRecord(id="A", values=[1, 2, 3]))
    attempts = []

    def change(tx):
        attempts.append(tx.get(ListRecord, "A").values)
        tx.patch(ListRecord, "A", changes={"values": [9]})
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(change)
    assert attempts == [[1, 2, 3], [1, 2, 3]]
    assert db.get(ListRecord, "A").values == [9]
    assert db.check_indexes(ListRecord).issues == ()
