# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import field_validator

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    MigrationError,
    SchemaMismatch,
    StorageError,
    TransactionError,
)
from mccann_hub.yhmf.maintenance import GATE, job_path
from mccann_hub.yhmf.schemas import schema_path

_models = runpy.run_path(str(FilePath(__file__).with_name("migration_models.py")))
Product, ProductV2 = _models["Product"], _models["ProductV2"]


def seed(store, count=3):
    old = Database(store, Registry(Product))
    for number in range(count):
        old.insert(
            Product(id=f"P{number}", name="Main", sku=f"SKU{number}", price=Decimal("12.3400"))
        )
    new = Database(store, Registry(ProductV2))
    return old, new


def begin(db):
    return db.begin_migration(Product, ProductV2, plan=db.plan_migration(ProductV2))


def advance_to(db, status, phase, source=Product, target=ProductV2, batch_size=1):
    for _ in range(100):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, source, target, batch_size=batch_size)
    raise AssertionError("Migration did not reach the requested phase")


def snapshot(store):
    result = {}

    def visit(path):
        value = store.get(path)
        if value is not None:
            result[path] = value
        for key in store.children(path, limit=100):
            visit(path.child(key))

    for name in (
        "^YHMFTestProduct",
        "^YHMFTestWarehouse",
        "^YHMFTestOrder",
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
        "^YHMFMigration",
    ):
        visit(Path(name))
    return result


def test_backfill_preserves_values_and_claims_and_publishes_only_at_activation(store):
    old, db = seed(store)
    before = snapshot(store)
    status = begin(db)
    assert status.phase == "validate" and status.validated == 0
    assert db.inspect_schema(ProductV2).migration_id == status.migration_id
    assert db.inspect_schema(ProductV2).installed.schema_version == 1
    with pytest.raises(MaintenanceActive):
        old.get(Product, "P0")
    status = advance_to(db, status, "backfill")
    assert status.validated == 3 and status.backfilled == 0
    assert store.get(Path(Product.__global__, (b"s:P0", b"note"))) is None
    status = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    assert status.backfilled == 1
    assert store.get(Path(Product.__global__, (b"s:P0", b"note"))) == b"n:"
    with pytest.raises(MigrationError):
        db.abort_migration(status.migration_id)
    # Reconstruct both the database and registry after an interruption.
    db = Database(store, Registry(ProductV2))
    assert db.migration_status(status.migration_id) == status
    status = advance_to(db, status, "activate")
    assert (status.validated, status.backfilled, status.verified, status.retagged) == (3, 3, 3, 3)
    assert db.inspect_schema(ProductV2).installed.schema_version == 1
    with pytest.raises(MaintenanceActive):
        db.get(ProductV2, "P0")
    status = db.advance_migration(status.migration_id, Product, ProductV2)
    assert status.phase == "complete" and store.get(GATE) is None
    assert db.inspect_schema(ProductV2).status == "matching"
    assert db.get(ProductV2, "P0").price.as_tuple() == Decimal("12.3400").as_tuple()
    assert db.get(ProductV2, "P0").quantity == 7
    assert len(db.query(ProductV2).where(note=None).all()) == 3
    assert len(db.query(ProductV2).where(name="Main").all()) == 3
    assert db.check_indexes(ProductV2).issues == ()
    with pytest.raises(SchemaMismatch):
        old.get(Product, "P0")
    after = snapshot(store)
    for path, value in before.items():
        if path.global_name == "^YHMFUnique" or (
            path.global_name == Product.__global__ and b"%m" not in path.subscripts
        ):
            assert after[path] == value
    assert db.advance_migration(status.migration_id, Product, ProductV2) == status


@pytest.mark.parametrize("phase", ["validate", "backfill"])
def test_abort_before_backfill_restores_source_and_changes_no_application_data(store, phase):
    old, db = seed(store)
    before = snapshot(store)
    status = advance_to(db, begin(db), phase)
    status = db.abort_migration(status.migration_id)
    assert status.phase == "aborted" and store.get(GATE) is None
    assert old.get(Product, "P0").name == "Main"
    after = snapshot(store)
    assert {
        path: value for path, value in after.items() if path.global_name != "^YHMFMigration"
    } == before
    assert db.abort_migration(status.migration_id) == status
    with pytest.raises(MigrationError):
        db.advance_migration(status.migration_id, Product, ProductV2)


def test_competing_start_and_unreviewed_or_changed_plans_are_rejected(store):
    _, db = seed(store)
    plan = db.plan_migration(ProductV2)
    before = snapshot(store)
    with pytest.raises(MigrationError):
        db.begin_migration(Product, ProductV2, plan=replace(plan, steps=()))
    assert snapshot(store) == before
    status = db.begin_migration(Product, ProductV2, plan=plan)
    with pytest.raises(MaintenanceActive):
        db.begin_migration(Product, ProductV2, plan=plan)
    assert db.migration_status(status.migration_id) == status


def test_stale_source_and_resume_bindings_leave_checkpoint_unchanged(store):
    _, db = seed(store)
    plan = db.plan_migration(ProductV2)

    class Different(Product):
        pass

    before = snapshot(store)
    with pytest.raises(MigrationError):
        db.begin_migration(Different, ProductV2, plan=plan)
    assert snapshot(store) == before
    status = db.begin_migration(Product, ProductV2, plan=plan)
    before = snapshot(store)
    with pytest.raises(MigrationError):
        db.advance_migration(status.migration_id, Different, ProductV2)
    assert snapshot(store) == before


@pytest.mark.parametrize("kind", ["validation", "removed", "factory", "type", "reference"])
def test_unsupported_changes_do_not_claim_maintenance(store, kind):
    seed(store)
    calls = []

    def factory():
        calls.append(1)
        return "generated"

    class Constrained(Product):
        name: str = Field(min_length=1)

    class Removed(Model):
        __global__ = Product.__global__
        id: str = Field(primary_key=True)

    class Dynamic(Product):
        note: str = Field(default_factory=factory)

    class Numeric(Product):
        name: int

    class Referenced(Product):
        parent: Reference[Product] | None = None

    target = {
        "validation": Constrained,
        "removed": Removed,
        "factory": Dynamic,
        "type": Numeric,
        "reference": Referenced,
    }[kind]
    # The reference variant targets another global to keep registration valid.
    if kind == "reference":

        class Order(Model):
            __global__ = "^YHMFTestOrder"
            id: str = Field(primary_key=True)

        class Referenced(Product):
            parent: Reference[Order] | None = None

        target = Referenced
        registry = Registry(Order, target)
    else:
        registry = Registry(target)
    db = Database(store, registry)
    plan = db.plan_migration(target)
    before = snapshot(store)
    assert not plan.execution_supported
    with pytest.raises(MigrationError):
        db.begin_migration(Product, target, plan=plan)
    assert snapshot(store) == before and calls == []


@pytest.mark.parametrize("size", [0, -1, 101, True, 1.5])
def test_invalid_batch_sizes_do_not_advance(store, size):
    _, db = seed(store)
    status = begin(db)
    with pytest.raises(MigrationError):
        db.advance_migration(status.migration_id, Product, ProductV2, batch_size=size)
    assert db.migration_status(status.migration_id) == status


def test_validation_failure_is_value_free_and_can_be_aborted(store):
    old, _ = seed(store)

    class Invalid(ProductV2):
        @field_validator("name")
        @classmethod
        def reject_name(cls, value):
            raise ValueError("private-value-marker")

    db = Database(store, Registry(Invalid))
    status = db.begin_migration(Product, Invalid, plan=db.plan_migration(Invalid))
    with pytest.raises(MigrationError) as error:
        db.advance_migration(status.migration_id, Product, Invalid)
    assert "private-value-marker" not in str(error.value)
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)
    assert old.get(Product, "P0").name == "Main"


def test_existing_field_changes_by_validators_are_rejected(store):
    seed(store)

    class Mutating(ProductV2):
        @field_validator("name")
        @classmethod
        def mutate_name(cls, value):
            return value.upper()

    db = Database(store, Registry(Mutating))
    status = db.begin_migration(Product, Mutating, plan=db.plan_migration(Mutating))
    with pytest.raises(MigrationError):
        db.advance_migration(status.migration_id, Product, Mutating)
    assert db.migration_status(status.migration_id) == status


@pytest.mark.parametrize("fault", ["field", "index", "checkpoint", "metadata", "activation"])
def test_failure_rolls_back_the_entire_batch_and_resumes(store, monkeypatch, fault):
    _, db = seed(store)
    phase = "retag" if fault == "metadata" else "activate" if fault == "activation" else "backfill"
    status = advance_to(db, begin(db), phase)
    before = snapshot(store)
    original = store.set
    tripped = []

    def fail_after_write(path, value):
        original(path, value)
        matches = {
            "field": path.global_name == Product.__global__ and path.subscripts[-1] == b"note",
            "index": path.global_name == "^YHMFIndex",
            "checkpoint": path == job_path(status.migration_id),
            "metadata": path.global_name == Product.__global__ and b"%m" in path.subscripts,
            "activation": path == schema_path(Product.__global__).child(b"snapshot"),
        }
        if matches[fault]:
            tripped.append(path)
            raise RuntimeError("Injected write failure")

    monkeypatch.setattr(store, "set", fail_after_write)
    with pytest.raises(RuntimeError, match="Injected write failure"):
        db.advance_migration(status.migration_id, Product, ProductV2, batch_size=2)
    assert tripped and snapshot(store) == before
    monkeypatch.setattr(store, "set", original)
    assert db.migration_status(status.migration_id) == status
    completed = advance_to(db, status, "complete", batch_size=2)
    assert completed.retagged == 3


def test_unmanaged_new_field_and_stale_new_index_are_not_overwritten(store):
    _, db = seed(store)
    field = Path(Product.__global__, (b"s:P0", b"note"))
    store.set(field.child(b"raw"), b"unmanaged")
    status = begin(db)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Product, ProductV2)
    db.abort_migration(status.migration_id)
    store.delete_tree(field)
    store.set(Path("^YHMFIndex", (b"YHMFTestProduct", b"note", b"raw")), b"unmanaged")
    with pytest.raises(StorageError):
        begin(db)
    assert store.get(GATE) is None


def test_lifecycle_requires_standalone_active_handles(store):
    _, db = seed(store)
    plan = db.plan_migration(ProductV2)
    handles = []

    def attempt(tx):
        handles.append(tx)
        with pytest.raises(TransactionError):
            tx.begin_migration(Product, ProductV2, plan=plan)

    db.atomic(attempt)
    with pytest.raises(TransactionError):
        handles[-1].begin_migration(Product, ProductV2, plan=plan)
    assert store.get(GATE) is None


def test_empty_installed_global_can_migrate(store):
    old, db = seed(store, count=1)
    old.delete(Product, "P0")
    status = advance_to(db, begin(db), "complete")
    assert status.retagged == 0 and db.scan(ProductV2) == []


def test_owned_values_and_existing_references_survive_migration(store):
    class Order(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)

    class Lot(Model):
        code: int = Field(primary_key=True)
        order: Reference[Order]
        amount: Decimal = Field(unique=True)

    class Warehouse(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        lots: Children[Lot] = ChildField(key="code")

    class WarehouseV2(Warehouse):
        tag: bytes = Field(default=b"ready", index=True)

    old = Database(store, Registry(Order, Warehouse))
    old.insert(Order(id="O1"))
    old.insert(
        Warehouse(id=1, lots={2: Lot(code=2, order=Reference(key="O1"), amount=Decimal("1.00"))})
    )
    before = snapshot(store)
    db = Database(store, Registry(Order, WarehouseV2))
    status = db.begin_migration(Warehouse, WarehouseV2, plan=db.plan_migration(WarehouseV2))
    status = advance_to(db, status, "complete", Warehouse, WarehouseV2)
    assert db.get(WarehouseV2, 1).lots[2].amount.as_tuple() == Decimal("1.00").as_tuple()
    assert db.query(WarehouseV2).where(tag=b"ready").all()[0].id == 1
    after = snapshot(store)
    assert {p: v for p, v in after.items() if p.global_name in {"^YHMFRef", "^YHMFUnique"}} == {
        p: v for p, v in before.items() if p.global_name in {"^YHMFRef", "^YHMFUnique"}
    }


def test_migration_rejects_corrupted_external_reference_target_schema(store):
    class Target(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class Source(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        target: Reference[Target]

    class Added(Source):
        __schema_version__ = 2
        note: str = "new"

    old = Database(store, Registry(Target, Source))
    old.insert(Target(id="P"))
    old.insert(Source(id="S", target=Reference[Target](key="P")))
    db = Database(store, Registry(Target, Added))
    status = db.begin_migration(Source, Added, plan=db.plan_migration(Added))
    target_schema = schema_path(Target.__global__)
    saved = store.get(target_schema.child(b"snapshot"))
    store.delete_tree(target_schema.child(b"snapshot"))
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Source, Added, batch_size=1)
    assert db.migration_status(status.migration_id) == status
    assert store.get(Path(Source.__global__, (b"s:S", b"note"))) is None
    store.set(target_schema.child(b"snapshot"), saved)
    progressed = db.advance_migration(status.migration_id, Source, Added, batch_size=1)
    assert progressed.validated == 1
    db.abort_migration(status.migration_id)
