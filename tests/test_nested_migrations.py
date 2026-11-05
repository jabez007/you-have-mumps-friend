# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import create_model, model_validator

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    MigrationError,
    SchemaMismatch,
    StorageError,
    UniqueViolation,
)
from mccann_hub.yhmf.maintenance import GATE, job_path

_models = runpy.run_path(str(FilePath(__file__).with_name("nested_migration_models.py")))
Target, Warehouse, WarehouseV2 = _models["Target"], _models["Warehouse"], _models["WarehouseV2"]


def seed(store):
    old = Database(store, Registry(Target, Warehouse))
    old.insert(Target(id="T1"))
    for record in _models["sample_records"]():
        old.insert(record)
    return old, Database(store, Registry(Target, WarehouseV2))


def begin(db, target=WarehouseV2):
    plan = db.plan_migration(target)
    assert plan.execution_supported
    return db.begin_migration(Warehouse, target, plan=plan)


def advance_to(db, status, phase, target=WarehouseV2):
    for _ in range(100):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, Warehouse, target, batch_size=1)
    raise AssertionError("Migration did not reach its requested phase")


def application_nodes(store):
    result = {}

    def visit(path):
        value = store.get(path)
        if value is not None:
            result[path] = value
        for key in store.children(path, limit=100):
            visit(path.child(key))

    for name in (Warehouse.__global__, Target.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
        visit(Path(name))
    return result


def test_nested_defaults_indexes_and_promotions_preserve_existing_owned_trees(store):
    old, db = seed(store)
    before = application_nodes(store)
    status = advance_to(db, begin(db), "backfill")
    assert application_nodes(store) == before
    assert status.validated == 3
    status = db.advance_migration(status.migration_id, Warehouse, WarehouseV2, batch_size=1)
    assert status.backfilled == 1
    root = Path(Warehouse.__global__, (b"s:W1",))
    assert store.get(root.child(b"lots", b"-1", b"note")) == b"n:"
    with pytest.raises(MaintenanceActive):
        old.get(Warehouse, "W1")
    db = Database(store, Registry(Target, WarehouseV2))
    status = advance_to(db, db.migration_status(status.migration_id), "complete")
    assert status.retagged == 3
    first = db.get(WarehouseV2, "W1")
    assert first.note == 9 and first.header.note == b"ready"
    assert first.header.precision.as_tuple() == Decimal("1.2300").as_tuple()
    assert first.lots[-1].note is None and first.lots[-1].detail.note == b"ready"
    assert first.lots[-1].samples["μ"].quality == 7
    assert first.lots[2].detail is None and first.lots[2].samples == {}
    assert db.get(WarehouseV2, "W2").header is None
    assert db.get(WarehouseV2, "W3").lots == {}
    for path, value in before.items():
        if b"%m" not in path.subscripts:
            assert store.get(path) == value
    assert db.check_indexes(WarehouseV2).issues == ()
    assert [r.id for r in db.query(WarehouseV2).where(**{"lots.note": None}).all()] == ["W1", "W2"]
    assert [r.id for r in db.query(WarehouseV2).where(**{"header.note": b"ready"}).all()] == ["W1"]
    assert [r.id for r in db.query(WarehouseV2).where(**{"lots.samples.quality": 7}).all()] == [
        "W1",
        "W2",
    ]
    assert (
        db.query(WarehouseV2).where(**{"lots.samples.reading": Decimal("1.230")}).all()[0].id
        == "W1"
    )
    with pytest.raises(SchemaMismatch):
        old.get(Warehouse, "W1")
    duplicate = first.model_copy(deep=True)
    duplicate.lots[2].serial = "private-A"
    with pytest.raises(UniqueViolation):
        db.save(duplicate)
    assert db.get(WarehouseV2, "W1").lots[2].serial is None


@pytest.mark.parametrize(
    "kind", ["serial_within", "serial_across", "sample_within", "sample_across"]
)
def test_nested_duplicates_are_detected_before_backfill_and_can_be_aborted(store, kind):
    old, db = seed(store)
    key = "W1" if kind.endswith("within") else "W2"
    record = old.get(Warehouse, key)
    if kind.startswith("serial"):
        record.lots[2 if key == "W1" else 0].serial = "private-A"
    else:
        record.lots[-1 if key == "W1" else 0].samples[
            "μ" if key == "W1" else "B"
        ].reading = Decimal("1.230")
    old.save(record)
    before = application_nodes(store)
    status = begin(db)
    with pytest.raises(UniqueViolation) as error:
        for _ in range(4):
            status = db.advance_migration(status.migration_id, Warehouse, WarehouseV2, batch_size=1)
    assert "private-A" not in str(error.value)
    assert db.migration_status(status.migration_id) == status
    assert status.backfilled == 0 and application_nodes(store) == before
    db.abort_migration(status.migration_id)
    assert old.get(Warehouse, key).id == key


@pytest.mark.parametrize(
    "phase,fault",
    [
        ("validate", "reservation"),
        ("backfill", "child_field"),
        ("backfill", "claim"),
        ("backfill", "checkpoint"),
        ("retag", "checkpoint"),
        ("activate", "checkpoint"),
    ],
)
def test_parent_trees_indexes_and_checkpoints_roll_back_together(store, monkeypatch, phase, fault):
    _, db = seed(store)
    status = advance_to(db, begin(db), phase)
    before = application_nodes(store)
    reservations = store.children(job_path(status.migration_id).child(b"unique"), limit=100)
    original = store.set
    observed = []

    def fail_after_write(path, value):
        original(path, value)
        matches = {
            "reservation": path.global_name == "^YHMFMigration" and len(path.subscripts) == 4,
            "child_field": path.global_name == Warehouse.__global__
            and len(path.subscripts) == 4
            and path.subscripts[-1] == b"note",
            "claim": path.global_name == "^YHMFUnique",
            "checkpoint": path == job_path(status.migration_id),
        }
        if matches[fault]:
            observed.append(path)
            raise RuntimeError("Injected nested migration failure")

    monkeypatch.setattr(store, "set", fail_after_write)
    with pytest.raises(RuntimeError, match="Injected nested"):
        db.advance_migration(status.migration_id, Warehouse, WarehouseV2, batch_size=2)
    monkeypatch.setattr(store, "set", original)
    assert observed and db.migration_status(status.migration_id) == status
    assert application_nodes(store) == before
    assert store.children(job_path(status.migration_id).child(b"unique"), limit=100) == reservations
    assert advance_to(db, status, "complete").retagged == 3


@pytest.mark.parametrize(
    "global_name,field", [("^YHMFIndex", b"lots.note"), ("^YHMFUnique", b"lots.serial")]
)
def test_full_nested_index_namespaces_must_be_empty_before_begin(store, global_name, field):
    _, db = seed(store)
    path = Path(global_name, (b"YHMFTestWarehouse", field, b"raw"))
    store.set(path, b"unmanaged")
    before = application_nodes(store)
    with pytest.raises(StorageError):
        begin(db)
    assert store.get(GATE) is None and application_nodes(store) == before


def test_occupied_nested_backfill_path_is_never_overwritten(store):
    old, db = seed(store)
    path = Path(Warehouse.__global__, (b"s:W1", b"lots", b"-1", b"note", b"raw"))
    store.set(path, b"unmanaged")
    before = application_nodes(store)
    status = begin(db)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Warehouse, WarehouseV2)
    assert application_nodes(store) == before
    db.abort_migration(status.migration_id)
    assert old.get(Warehouse, "W1").lots[-1].serial == "private-A"


def test_nested_default_factory_is_rejected_even_when_no_owner_exists(store):
    old = Database(store, Registry(Target, Warehouse))
    old.insert(Warehouse(id="empty", locked="empty"))
    calls = []

    def factory():
        calls.append(1)
        return "generated"

    class DynamicLot(_models["Lot"]):
        added: str = Field(default_factory=factory)

    class DynamicWarehouse(Warehouse):
        lots: Children[DynamicLot] = ChildField(key="code")

    db = Database(store, Registry(Target, DynamicWarehouse))
    plan = db.plan_migration(DynamicWarehouse)
    assert not plan.execution_supported and plan.requires_manual_review
    with pytest.raises(MigrationError):
        db.begin_migration(Warehouse, DynamicWarehouse, plan=plan)
    assert calls == [] and store.get(GATE) is None


def test_nullable_collections_remain_null_instead_of_materializing_children(store):
    Lot, LotV2 = _models["Lot"], _models["LotV2"]

    class Before(Warehouse):
        lots: Children[Lot] | None = ChildField(default=None, key="code")

    class After(Before):
        lots: Children[LotV2] | None = ChildField(default=None, key="code")

    old = Database(store, Registry(Target, Before))
    old.insert(Before(id="null", locked="null"))
    db = Database(store, Registry(Target, After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    while status.phase != "complete":
        status = db.advance_migration(status.migration_id, Before, After, batch_size=1)
    assert db.get(After, "null").lots is None
    assert store.get(Path(Warehouse.__global__, (b"s:null", b"lots"))) == b"n:"


def test_target_validators_cannot_materialize_absent_owned_objects(store):
    old, _ = seed(store)

    class Materializing(WarehouseV2):
        @model_validator(mode="after")
        def materialize_header(self):
            if self.header is None:
                self.header = _models["DetailV2"](label="created", weight=Decimal(1))
            return self

    db = Database(store, Registry(Target, Materializing))
    status = begin(db, Materializing)
    status = db.advance_migration(status.migration_id, Warehouse, Materializing, batch_size=1)
    with pytest.raises(MigrationError, match="existing field"):
        db.advance_migration(status.migration_id, Warehouse, Materializing, batch_size=1)
    db.abort_migration(status.migration_id)
    assert old.get(Warehouse, "W2").header is None


@pytest.mark.parametrize("new_fields", [9, 11])
def test_nested_backfills_enforce_record_node_limits_before_data_mutation(store, new_fields):
    class Item(Model):
        key: int = Field(primary_key=True)

    class Before(Model):
        __global__ = Warehouse.__global__
        id: int = Field(primary_key=True)
        items: Children[Item] = ChildField(key="key")

    NewItem = create_model(
        "NewItem", __base__=Item, **{f"v{i}": (int, 1) for i in range(new_fields)}
    )

    class After(Before):
        items: Children[NewItem] = ChildField(key="key")

    old = Database(store, Registry(Before))
    old.insert(Before(id=1, items={i: Item(key=i) for i in range(1000)}))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    with pytest.raises(StorageError, match="node budget"):
        db.advance_migration(status.migration_id, Before, After, batch_size=1)
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)
    assert len(old.get(Before, 1).items) == 1000
