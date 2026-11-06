# SPDX-License-Identifier: GPL-3.0-or-later
import json
import runpy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import field_validator

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    MigrationError,
    StorageError,
    TransactionError,
    UniqueViolation,
)
from mccann_hub.yhmf.maintenance import GATE, job_path
from mccann_hub.yhmf.migration_cleanup import removed_count

_models = runpy.run_path(str(FilePath(__file__).with_name("removal_migration_models.py")))
Product, Warehouse, Target = _models["Product"], _models["Warehouse"], _models["RelatedTarget"]


def seed(store, nested=False):
    if nested:
        db = Database(store, Registry(Target, Warehouse))
        db.insert(Target(id="T1"))
        for record in _models["nested_records"]():
            db.insert(record)
        return db, Warehouse
    db = Database(store, Registry(Product))
    for number, sku in enumerate(("private-A", None, None)):
        db.insert(
            Product(
                id=f"P{number}",
                sku=sku,
                name="Main",
                locked=f"L{number}",
                price=Decimal(f"{number + 1}.00"),
            )
        )
    return db, Product


def advance_to(db, status, model, phase, batch_size=1):
    for _ in range(300):
        if status.phase == phase:
            return status
        status = db.advance_index_rebuild(status.migration_id, model, batch_size=batch_size)
    raise AssertionError("Rebuild did not reach its requested phase")


def all_nodes(store):
    result = {}

    def visit(path):
        value = store.get(path)
        if value is not None:
            result[path] = value
        for key in store.children(path, limit=100):
            visit(path.child(key))

    for name in (
        Product.__global__,
        Warehouse.__global__,
        Target.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    ):
        visit(Path(name))
    return result


def test_rebuild_plan_is_read_only_deterministic_and_value_free(store, read_only_store):
    seed(store)
    db = Database(read_only_store, Registry(Product))
    before = all_nodes(store)
    plan = db.plan_index_rebuild(Product)
    assert plan.execution_supported
    assert plan.fields == ("locked", "name", "price", "sku")
    assert plan.unique_fields == ("locked", "price", "sku")
    assert plan.to_json() == db.plan_index_rebuild(Product).to_json()
    assert "private-A" not in plan.to_json() and "private-A" not in repr(plan)
    assert json.loads(plan.to_json())["operation"] == "index_rebuild"
    assert all_nodes(store) == before


@pytest.mark.parametrize("nested", [False, True])
def test_rebuild_repairs_missing_corrupt_and_orphan_nodes_without_changing_records_or_schema(
    store, nested
):
    db, model = seed(store, nested)
    before = all_nodes(store)
    global_key = model.__global__[1:].encode()
    for global_name in ("^YHMFIndex", "^YHMFUnique"):
        store.delete_tree(Path(global_name, (global_key,)))
        store.set(
            Path(global_name, (global_key, b"sku" if not nested else b"lots.serial", b"orphan")),
            b"corrupt",
        )
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    assert status.source_fingerprint == status.target_fingerprint
    assert status.operation_fingerprint is not None
    assert db.inspect_schema(model).status == "matching"
    assert db.inspect_schema(model).migration_id == status.migration_id
    with pytest.raises(MaintenanceActive):
        db.get(model, "W1" if nested else "P0")
    status = advance_to(db, status, model, "clear_indexes")
    assert status.validated == 3 and status.backfilled == 0
    db = Database(store, Registry(Target, model) if nested else Registry(model))
    status = advance_to(
        db, db.migration_status(status.migration_id), model, "complete", batch_size=2
    )
    assert status.validated == status.backfilled == status.verified == 3 and status.retagged == 0
    assert all_nodes(store) == before
    assert db.check_indexes(model).issues == ()
    assert store.get(GATE) is None
    assert db.advance_index_rebuild(status.migration_id, model) == status
    if nested:
        assert (
            db.query(model).where(**{"lots.samples.reading": Decimal("1.23")}).all()[0].id == "W1"
        )
        assert db.get(model, "W2").header is None
    else:
        assert len(db.query(model).where(sku=None).all()) == 2


def test_selected_rebuild_preserves_unselected_namespaces_and_validates_reviewed_selection(store):
    db, model = seed(store)
    untouched = Path("^YHMFIndex", (b"YHMFTestProduct", b"name", b"unselected-orphan"))
    store.set(untouched, b"preserve")
    plan = db.plan_index_rebuild(model, fields=("sku",))
    with pytest.raises(MigrationError):
        db.begin_index_rebuild(model, plan=replace(plan, unique_fields=()))
    status = db.begin_index_rebuild(model, plan=plan)
    advance_to(db, status, model, "complete")
    assert store.get(untouched) == b"preserve"
    assert db.query(model).where(sku="private-A").all()[0].id == "P0"


def test_selected_nested_reference_equality_index_can_be_rebuilt_without_reference_changes(store):
    db, model = seed(store, True)
    before = all_nodes(store)
    store.delete_tree(Path("^YHMFIndex", (b"YHMFTestWarehouse", b"lots.target")))
    plan = db.plan_index_rebuild(model, fields=("lots.target",))
    status = db.begin_index_rebuild(model, plan=plan)
    advance_to(db, status, model, "complete")
    assert all_nodes(store) == before


@pytest.mark.parametrize("nested", [False, True])
def test_uniqueness_conflicts_are_found_before_clearing_and_allow_abort(store, nested):
    db, model = seed(store, nested)
    if nested:
        path = Path(model.__global__, (b"s:W1", b"lots", b"2", b"serial"))
        store.set(path, b"s:private-A")
    else:
        store.set(Path(model.__global__, (b"s:P1", b"sku")), b"s:private-A")
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    before = all_nodes(store)
    with pytest.raises(UniqueViolation) as error:
        for _ in range(5):
            status = db.advance_index_rebuild(status.migration_id, model, batch_size=1)
    assert "private-A" not in str(error.value)
    assert all_nodes(store) == before and removed_count(store, status.migration_id) == 0
    assert db.migration_status(status.migration_id) == status
    assert db.abort_migration(status.migration_id).phase == "aborted"
    assert store.get(GATE) is None


@pytest.mark.parametrize(
    "phase,fault",
    [
        ("validate", "checkpoint"),
        ("clear_indexes", "delete"),
        ("clear_indexes", "checkpoint"),
        ("backfill", "index"),
        ("backfill", "claim"),
        ("backfill", "checkpoint"),
        ("activate", "checkpoint"),
    ],
)
def test_rebuild_data_and_checkpoint_rollback_together(store, monkeypatch, phase, fault):
    db, model = seed(store)
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    status = advance_to(db, status, model, phase)
    before, count = all_nodes(store), removed_count(store, status.migration_id)
    original_set, original_delete = store.set, store.delete_tree
    observed = []

    def fail_set(path, value):
        original_set(path, value)
        matches = {
            "index": path.global_name == "^YHMFIndex",
            "claim": path.global_name == "^YHMFUnique",
            "checkpoint": path == job_path(status.migration_id),
            "delete": False,
        }
        if matches[fault]:
            observed.append(path)
            raise RuntimeError("Injected rebuild failure")

    def fail_delete(path):
        original_delete(path)
        if fault == "delete":
            observed.append(path)
            raise RuntimeError("Injected rebuild failure")

    monkeypatch.setattr(store, "set", fail_set)
    monkeypatch.setattr(store, "delete_tree", fail_delete)
    with pytest.raises(RuntimeError, match="Injected rebuild"):
        db.advance_index_rebuild(status.migration_id, model, batch_size=2)
    monkeypatch.setattr(store, "set", original_set)
    monkeypatch.setattr(store, "delete_tree", original_delete)
    assert observed and all_nodes(store) == before
    assert db.migration_status(status.migration_id) == status
    assert removed_count(store, status.migration_id) == count
    advance_to(db, status, model, "complete")


def test_clear_is_leaf_bounded_and_abort_is_refused_after_a_committed_delete(store, monkeypatch):
    db, model = seed(store)
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    status = advance_to(db, status, model, "clear_indexes")
    deleted = []
    original = store.delete_tree

    def capture(path):
        assert store.children(path, limit=1) == ()
        deleted.append(path)
        original(path)

    monkeypatch.setattr(store, "delete_tree", capture)
    status = db.advance_index_rebuild(status.migration_id, model, batch_size=2)
    assert len(deleted) == removed_count(store, status.migration_id) == 2
    with pytest.raises(MigrationError):
        db.abort_migration(status.migration_id)
    monkeypatch.setattr(store, "delete_tree", original)
    advance_to(db, status, model, "complete")


def test_rebuild_selection_drift_and_wrong_executor_are_rejected(store):
    db, model = seed(store)
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model, fields=("sku",)))
    root = job_path(status.migration_id).child(b"operation")
    original = store.get(root)
    store.set(
        root, canonical_json({"format_version": 1, "kind": "index_rebuild", "fields": ["name"]})
    )
    with pytest.raises(StorageError, match="selection"):
        db.advance_index_rebuild(status.migration_id, model)
    store.set(root, original)

    class Later(model):
        note: str | None = None

    migration_db = Database(store, Registry(Later))
    with pytest.raises(MigrationError, match="executor"):
        migration_db.advance_migration(status.migration_id, model, Later)
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)
    migration = migration_db.begin_migration(model, Later, plan=migration_db.plan_migration(Later))
    with pytest.raises(MigrationError, match="not an index rebuild"):
        db.advance_index_rebuild(migration.migration_id, model)
    migration_db.abort_migration(migration.migration_id)


@pytest.mark.parametrize("fields", [(), ("missing",), ("sku", "sku"), "sku", (1,)])
def test_invalid_selection_never_claims_maintenance(store, fields):
    db, model = seed(store)
    with pytest.raises(MigrationError):
        db.plan_index_rebuild(model, fields=fields)
    assert store.get(GATE) is None


def test_rebuild_requires_matching_installed_schema_and_active_standalone_handles(store):
    db = Database(store, Registry(Product))
    plan = db.plan_index_rebuild(Product)
    assert not plan.execution_supported
    with pytest.raises(MigrationError):
        db.begin_index_rebuild(Product, plan=plan)
    db, model = seed(store)
    plan = db.plan_index_rebuild(model)

    def attempt(tx):
        with pytest.raises(TransactionError):
            tx.begin_index_rebuild(model, plan=plan)
        return tx

    expired = db.atomic(attempt)
    with pytest.raises(TransactionError):
        expired.plan_index_rebuild(model)
    assert store.get(GATE) is None


def test_empty_installed_model_with_orphans_can_be_rebuilt(store):
    db, model = seed(store)
    for number in range(3):
        db.delete(model, f"P{number}")
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"sku", b"orphan"))
    store.set(root, b"orphan")
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    status = advance_to(db, status, model, "complete")
    assert status.validated == status.backfilled == status.verified == 0
    assert store.get(root) is None and db.scan(model) == []


def test_changed_validators_cannot_rewrite_authoritative_record_values(store):
    class Validated(Product):
        @field_validator("name")
        @classmethod
        def normalize(cls, value):
            return value.upper()

    db = Database(store, Registry(Validated))
    db.insert(Validated(id="P0", sku="A", price=Decimal(1), name="Main", locked="L0"))
    store.set(Path(Product.__global__, (b"s:P0", b"name")), b"s:Main")
    status = db.begin_index_rebuild(Validated, plan=db.plan_index_rebuild(Validated))
    with pytest.raises(MigrationError, match="stored fields"):
        db.advance_index_rebuild(status.migration_id, Validated)
    db.abort_migration(status.migration_id)


def test_missing_record_fields_are_not_repaired_from_declared_defaults(store):
    db, model = seed(store)
    store.delete_tree(Path(Product.__global__, (b"s:P0", b"sku")))
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    before = all_nodes(store)
    with pytest.raises(StorageError):
        db.advance_index_rebuild(status.migration_id, model)
    assert all_nodes(store) == before and removed_count(store, status.migration_id) == 0
    db.abort_migration(status.migration_id)


def test_verification_checks_rebuilt_claims_before_activation(store):
    db, model = seed(store)
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    status = advance_to(db, status, model, "verify")
    root = Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b'v:"s:private-A"'))
    value = store.get(root)
    store.delete_tree(root)
    with pytest.raises(StorageError):
        db.advance_index_rebuild(status.migration_id, model, batch_size=1)
    assert db.migration_status(status.migration_id) == status and store.get(GATE) is not None
    store.set(root, value)
    advance_to(db, status, model, "complete")


@pytest.mark.parametrize("batch_size", [0, 101, True])
def test_invalid_batch_sizes_cannot_progress_a_rebuild(store, batch_size):
    db, model = seed(store)
    status = db.begin_index_rebuild(model, plan=db.plan_index_rebuild(model))
    with pytest.raises(MigrationError):
        db.advance_index_rebuild(status.migration_id, model, batch_size=batch_size)
    assert db.migration_status(status.migration_id) == status


def test_model_without_indexes_has_an_unsupported_rebuild_plan(store):
    class Plain(Model):
        __global__ = Product.__global__
        id: int = Field(primary_key=True)

    db = Database(store, Registry(Plain))
    db.insert(Plain(id=1))
    plan = db.plan_index_rebuild(Plain)
    assert not plan.execution_supported and plan.fields == ()
    with pytest.raises(MigrationError):
        db.begin_index_rebuild(Plain, plan=plan)
    assert store.get(GATE) is None


def test_changed_registration_cannot_begin_a_stale_rebuild(store):
    db, model = seed(store)
    plan = db.plan_index_rebuild(model)

    class Renamed(model):
        pass

    other = Database(store, Registry(Renamed))
    assert not other.plan_index_rebuild(Renamed).execution_supported
    with pytest.raises(MigrationError):
        other.begin_index_rebuild(Renamed, plan=plan)
    assert store.get(GATE) is None


def test_legacy_format_one_schema_jobs_still_resume_without_operation_metadata(store):
    db, model = seed(store)

    class Later(model):
        note: str | None = None

    newer = Database(store, Registry(Later))
    status = newer.begin_migration(model, Later, plan=newer.plan_migration(Later))
    document = json.loads(store.get(job_path(status.migration_id)))
    assert document["format_version"] == 1 and "operation_fingerprint" not in document
    assert store.get(job_path(status.migration_id).child(b"operation")) is None
    store.set(job_path(status.migration_id), canonical_json(document))
    newer = Database(store, Registry(Later))
    status = newer.migration_status(status.migration_id)
    while status.phase != "complete":
        status = newer.advance_migration(status.migration_id, model, Later, batch_size=1)
    assert newer.get(Later, "P0").note is None
