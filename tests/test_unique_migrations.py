# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import MaintenanceActive, SchemaMismatch, StorageError, UniqueViolation
from mccann_hub.yhmf.maintenance import GATE, job_path

_models = runpy.run_path(str(FilePath(__file__).with_name("unique_migration_models.py")))
Product, UniqueProduct = _models["Product"], _models["UniqueProduct"]


def seed(store, skus=("private-sku", None, None)):
    old = Database(store, Registry(Product))
    for number, sku in enumerate(skus):
        old.insert(
            Product(id=f"P{number}", sku=sku, serial=f"S{number}", price=Decimal(number + 1))
        )
    return old, Database(store, Registry(UniqueProduct))


def begin(db):
    plan = db.plan_migration(UniqueProduct)
    assert plan.execution_supported
    return db.begin_migration(Product, UniqueProduct, plan=plan)


def advance_to(db, status, phase, source=Product, target=UniqueProduct):
    for _ in range(100):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, source, target, batch_size=1)
    raise AssertionError("Migration did not reach its requested phase")


def application_nodes(store):
    result = {}

    def visit(path):
        value = store.get(path)
        if value is not None:
            result[path] = value
        for key in store.children(path, limit=100):
            visit(path.child(key))

    for name in (Product.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
        visit(Path(name))
    return result


def test_unique_addition_and_promotion_preserve_data_and_enforce_future_writes(store, monkeypatch):
    old, db = seed(store)
    before = application_nodes(store)
    status = advance_to(db, begin(db), "backfill")
    assert status.validated == 3 and status.backfilled == 0
    assert application_nodes(store) == before
    assert len(store.children(job_path(status.migration_id).child(b"unique"), limit=10)) == 4
    writes = []
    original_set = store.set
    original_delete = store.delete_tree

    def capture_set(path, value):
        writes.append(path)
        original_set(path, value)

    def capture_delete(path):
        assert not any(
            existing.global_name == path.global_name
            and existing.subscripts[: len(path.subscripts)] == path.subscripts
            for existing in before
            if existing.global_name == "^YHMFIndex"
        )
        original_delete(path)

    monkeypatch.setattr(store, "set", capture_set)
    monkeypatch.setattr(store, "delete_tree", capture_delete)
    db = Database(store, Registry(UniqueProduct))
    status = advance_to(db, status, "complete")
    assert status.retagged == 3
    assert not {p for p in before if p.global_name == "^YHMFIndex"} & set(writes)
    for path, value in before.items():
        if b"%m" not in path.subscripts:
            assert store.get(path) == value
    assert db.check_indexes(UniqueProduct).issues == ()
    monkeypatch.setattr(store, "set", original_set)
    monkeypatch.setattr(store, "delete_tree", original_delete)
    assert [item.id for item in db.query(UniqueProduct).where(sku="private-sku").all()] == ["P0"]
    assert len(db.query(UniqueProduct).where(sku=None).all()) == 2
    with pytest.raises(UniqueViolation):
        db.insert(UniqueProduct(id="duplicate", sku="private-sku", serial="new", price=Decimal(4)))
    with pytest.raises(UniqueViolation):
        db.insert(UniqueProduct(id="duplicate", sku="new", serial="new", price=Decimal("1.00")))
    db.save(UniqueProduct(id="P0", sku="replacement", serial="S0", price=Decimal("1.00")))
    db.insert(UniqueProduct(id="P3", sku="private-sku", serial="S3", price=Decimal("4")))
    db.delete(UniqueProduct, "P3")
    db.insert(UniqueProduct(id="P4", sku="private-sku", serial="S4", price=Decimal("4.0")))
    with pytest.raises(SchemaMismatch):
        old.get(Product, "P0")


@pytest.mark.parametrize("batch_size", [1, 100])
def test_duplicates_leave_application_data_untouched_and_abort_restores_source(store, batch_size):
    old, db = seed(store, ("private-sku", "private-sku"))
    before = application_nodes(store)
    status = begin(db)
    if batch_size == 1:
        status = db.advance_migration(status.migration_id, Product, UniqueProduct, batch_size=1)
    reservations = store.children(job_path(status.migration_id).child(b"unique"), limit=10)
    with pytest.raises(UniqueViolation) as error:
        db.advance_migration(status.migration_id, Product, UniqueProduct, batch_size=batch_size)
    assert "private-sku" not in str(error.value)
    assert "private-sku" not in repr(status) and "private-sku" not in str(status.to_dict())
    assert db.migration_status(status.migration_id) == status
    assert application_nodes(store) == before
    assert store.children(job_path(status.migration_id).child(b"unique"), limit=10) == reservations
    with pytest.raises(MaintenanceActive):
        old.get(Product, "P0")
    assert db.abort_migration(status.migration_id).phase == "aborted"
    assert old.get(Product, "P0").sku == old.get(Product, "P1").sku
    old.insert(Product(id="P3", sku="private-sku", serial="S3", price=Decimal(3)))


@pytest.mark.parametrize(
    "value_type,first,second,duplicate",
    [
        (Decimal, Decimal("1.00"), Decimal("1.0"), True),
        (float, -0.0, 0.0, True),
        (str, "exact", "EXACT", False),
    ],
)
def test_preflight_uses_normal_write_equality_rules(store, value_type, first, second, duplicate):
    class Before(Model):
        __global__ = Product.__global__
        id: int = Field(primary_key=True)
        value: value_type = Field(index=True)

    class After(Before):
        value: value_type = Field(unique=True)

    old = Database(store, Registry(Before))
    old.insert(Before(id=1, value=first))
    old.insert(Before(id=2, value=second))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    status = db.advance_migration(status.migration_id, Before, After, batch_size=1)
    if duplicate:
        with pytest.raises(UniqueViolation):
            db.advance_migration(status.migration_id, Before, After, batch_size=1)
        db.abort_migration(status.migration_id)
        assert old.get(Before, 1).value == first
    else:
        advance_to(db, status, "complete", Before, After)
        assert db.get(After, 1).value == first and db.get(After, 2).value == second


@pytest.mark.parametrize("phase", ["validate", "backfill", "activate"])
def test_reservations_claims_and_checkpoints_roll_back_together(store, monkeypatch, phase):
    _, db = seed(store)
    status = advance_to(db, begin(db), phase)
    before = application_nodes(store)
    original = store.set

    def fail_checkpoint(path, value):
        original(path, value)
        if path == job_path(status.migration_id):
            raise RuntimeError("Injected checkpoint failure")

    monkeypatch.setattr(store, "set", fail_checkpoint)
    with pytest.raises(RuntimeError, match="Injected checkpoint"):
        db.advance_migration(status.migration_id, Product, UniqueProduct, batch_size=2)
    monkeypatch.setattr(store, "set", original)
    assert db.migration_status(status.migration_id) == status
    assert application_nodes(store) == before
    if phase == "validate":
        assert store.children(job_path(status.migration_id).child(b"unique"), limit=10) == ()
    assert advance_to(db, status, "complete").retagged == 3


def test_corrupt_reservation_blocks_backfill_before_application_writes(store):
    _, db = seed(store)
    status = advance_to(db, begin(db), "backfill")
    root = job_path(status.migration_id).child(b"unique")
    reservation = root.child(store.children(root, limit=10)[0])
    store.set(reservation, b"corrupt")
    before = application_nodes(store)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Product, UniqueProduct, batch_size=100)
    assert db.migration_status(status.migration_id) == status
    assert application_nodes(store) == before
    db.abort_migration(status.migration_id)


def test_existing_claim_namespace_is_never_adopted(store):
    _, db = seed(store)
    store.set(Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b"orphan")), b"raw")
    before = application_nodes(store)
    with pytest.raises(StorageError):
        begin(db)
    assert store.get(GATE) is None and application_nodes(store) == before


def test_missing_built_claim_blocks_verification_and_activation(store):
    _, db = seed(store)
    status = advance_to(db, begin(db), "verify")
    claim = Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b'v:"s:private-sku"'))
    owner = store.get(claim)
    store.delete_tree(claim)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Product, UniqueProduct, batch_size=1)
    assert db.migration_status(status.migration_id) == status
    assert store.get(GATE) == status.migration_id.encode()
    store.set(claim, owner)
    assert advance_to(db, status, "complete").retagged == 3
