# SPDX-License-Identifier: GPL-3.0-or-later
import runpy
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import MaintenanceActive, MigrationError, QueryError, StorageError
from mccann_hub.yhmf.maintenance import GATE, job_path
from mccann_hub.yhmf.migration_cleanup import removed_count, retired_roots

_models = runpy.run_path(str(FilePath(__file__).with_name("removal_migration_models.py")))
Product, Relaxed, Unindexed = (
    _models["Product"],
    _models["RelaxedProduct"],
    _models["UnindexedProduct"],
)
Warehouse, RelaxedWarehouse, Target = (
    _models["Warehouse"],
    _models["RelaxedWarehouse"],
    _models["RelatedTarget"],
)


def root_seed(store):
    old = Database(store, Registry(Product))
    for number, sku in enumerate(("private-A", "private-B", None)):
        old.insert(
            Product(
                id=f"P{number}",
                sku=sku,
                name="Main",
                locked=f"L{number}",
                price=Decimal(f"{number + 1}.00"),
            )
        )
    return old, Database(store, Registry(Relaxed))


def nested_seed(store):
    old = Database(store, Registry(Target, Warehouse))
    old.insert(Target(id="T1"))
    for record in _models["nested_records"]():
        old.insert(record)
    return old, Database(store, Registry(Target, RelaxedWarehouse))


def begin(db, source=Product, target=Relaxed):
    plan = db.plan_migration(target)
    assert plan.execution_supported
    return db.begin_migration(source, target, plan=plan)


def advance_to(db, status, phase, source=Product, target=Relaxed, batch_size=1):
    for _ in range(200):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, source, target, batch_size=batch_size)
    raise AssertionError("Migration did not reach its requested phase")


def data_nodes(store):
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
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    ):
        visit(Path(name))
    return result


def test_unique_downgrade_and_complete_removal_preserve_values_and_query_behavior(store):
    old, db = root_seed(store)
    before = data_nodes(store)
    status = begin(db)
    plan = db.plan_migration(Relaxed)
    assert any(step.kind == "drop_uniqueness" and step.path == ("sku",) for step in plan.steps)
    status = advance_to(db, status, "backfill")
    assert data_nodes(store) == before
    status = db.advance_migration(status.migration_id, Product, Relaxed, batch_size=1)
    with pytest.raises(MaintenanceActive):
        old.get(Product, "P0")
    with pytest.raises(MigrationError):
        db.abort_migration(status.migration_id)
    db = Database(store, Registry(Relaxed))
    status = advance_to(db, db.migration_status(status.migration_id), "complete")
    assert status.retagged == 3 and db.check_indexes(Relaxed).issues == ()
    after = data_nodes(store)
    for path, value in before.items():
        if path.global_name == Product.__global__ and b"%m" not in path.subscripts:
            assert after[path] == value
        if path.global_name == "^YHMFIndex" and path.subscripts[1] == b"sku":
            assert after[path] == value
    db.insert(Relaxed(id="P3", sku="private-A", name="Main", price=Decimal("1.0"), locked="L3"))
    assert [r.id for r in db.query(Relaxed).where(sku="private-A").all()] == ["P0", "P3"]
    for field in ("name", "price"):
        with pytest.raises(QueryError):
            db.query(Relaxed).where(**{field: "Main"})
    final_db = Database(store, Registry(Unindexed))
    second = begin(final_db, Relaxed, Unindexed)
    advance_to(final_db, second, "complete", Relaxed, Unindexed)
    assert final_db.get(Unindexed, "P0").sku == "private-A"
    assert store.children(Path("^YHMFIndex", (b"YHMFTestProduct", b"sku")), limit=1) == ()
    with pytest.raises(QueryError):
        final_db.query(Unindexed).where(sku="private-A")


def test_nested_downgrades_keep_equality_entries_references_and_child_identities(store):
    _, db = nested_seed(store)
    before = data_nodes(store)
    status = begin(db, Warehouse, RelaxedWarehouse)
    advance_to(db, status, "complete", Warehouse, RelaxedWarehouse)
    after = data_nodes(store)
    for path, value in before.items():
        if (
            path.global_name in {Warehouse.__global__, Target.__global__}
            and b"%m" not in path.subscripts
        ):
            assert after[path] == value
        if path.global_name == "^YHMFRef":
            assert after[path] == value
        if path.global_name == "^YHMFIndex" and path.subscripts[1] in {
            b"lots.serial",
            b"lots.samples.reading",
        }:
            assert after[path] == value
    record = db.get(RelaxedWarehouse, "W1")
    record.lots[2].serial = record.lots[-1].serial
    record.lots[-1].samples["μ"].reading = Decimal("1.230")
    db.save(record)
    assert [
        r.id for r in db.query(RelaxedWarehouse).where(**{"lots.serial": "private-A"}).all()
    ] == ["W1"]
    assert (
        db.query(RelaxedWarehouse).where(**{"lots.samples.reading": Decimal("1.23")}).all()[0].id
        == "W1"
    )
    with pytest.raises(QueryError):
        db.query(RelaxedWarehouse).where(**{"lots.samples.quality": 7})
    assert db.check_indexes(RelaxedWarehouse).issues == ()


def test_cleanup_is_leaf_bounded_resumable_and_scoped_to_retired_namespaces(store, monkeypatch):
    _, db = root_seed(store)
    orphan = Path("^YHMFIndex", (b"YHMFTestProduct", b"name"))
    store.set(orphan, b"orphan root")
    for number in range(7):
        store.set(orphan.child(b"orphan", str(number).encode(), b"leaf"), b"orphan entry")
    keep = Path("^YHMFIndex", (b"YHMFTestProduct", b"sku", b"unrelated-orphan"))
    store.set(keep, b"preserved")
    status = advance_to(db, begin(db), "cleanup")
    deletes = []
    original = store.delete_tree

    def only_leaves(path):
        assert store.children(path, limit=1) == ()
        deletes.append(path)
        original(path)

    monkeypatch.setattr(store, "delete_tree", only_leaves)
    status = db.advance_migration(status.migration_id, Product, Relaxed, batch_size=2)
    assert status.phase == "cleanup" and len(deletes) == 2
    assert removed_count(store, status.migration_id) == 2
    db = Database(store, Registry(Relaxed))
    status = advance_to(db, db.migration_status(status.migration_id), "activate", batch_size=2)
    assert removed_count(store, status.migration_id) == 8
    monkeypatch.setattr(store, "delete_tree", original)
    advance_to(db, status, "complete")
    assert store.get(keep) == b"preserved"
    assert store.get(orphan) is None and store.children(orphan, limit=1) == ()


@pytest.mark.parametrize(
    "phase,fault",
    [
        ("backfill", "entry"),
        ("backfill", "claim"),
        ("backfill", "checkpoint"),
        ("cleanup", "leaf"),
        ("cleanup", "counter"),
        ("cleanup", "checkpoint"),
        ("activate", "checkpoint"),
    ],
)
def test_failed_removal_and_cleanup_batches_roll_back_every_change(
    store, monkeypatch, phase, fault
):
    _, db = root_seed(store)
    orphan = Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b"orphan"))
    store.set(orphan, b"orphan")
    status = advance_to(db, begin(db), phase)
    before = data_nodes(store)
    counter = removed_count(store, status.migration_id)
    original_delete, original_set = store.delete_tree, store.set
    tripped = []

    def fail_delete(path):
        original_delete(path)
        if (
            fault == "entry"
            and path.global_name == "^YHMFIndex"
            or fault == "claim"
            and path.global_name == "^YHMFUnique"
            or fault == "leaf"
        ):
            tripped.append(path)
            raise RuntimeError("Injected removal failure")

    def fail_set(path, value):
        original_set(path, value)
        if (
            fault == "checkpoint"
            and path == job_path(status.migration_id)
            or fault == "counter"
            and path == job_path(status.migration_id).child(b"cleaned_nodes")
        ):
            tripped.append(path)
            raise RuntimeError("Injected removal failure")

    monkeypatch.setattr(store, "delete_tree", fail_delete)
    monkeypatch.setattr(store, "set", fail_set)
    with pytest.raises(RuntimeError, match="Injected removal"):
        db.advance_migration(status.migration_id, Product, Relaxed, batch_size=2)
    monkeypatch.setattr(store, "delete_tree", original_delete)
    monkeypatch.setattr(store, "set", original_set)
    assert tripped and data_nodes(store) == before
    assert removed_count(store, status.migration_id) == counter
    assert db.migration_status(status.migration_id) == status
    assert advance_to(db, status, "complete").retagged == 3


def test_empty_global_orphan_cleanup_cannot_be_aborted_after_a_committed_delete(store):
    old, db = root_seed(store)
    for number in range(3):
        old.delete(Product, f"P{number}")
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"name"))
    store.set(root.child(b"a"), b"orphan")
    store.set(root.child(b"b"), b"orphan")
    status = advance_to(db, begin(db), "cleanup")
    assert status.backfilled == status.retagged == 0
    status = db.advance_migration(status.migration_id, Product, Relaxed, batch_size=1)
    assert removed_count(store, status.migration_id) == 1
    with pytest.raises(MigrationError):
        db.abort_migration(status.migration_id)
    assert advance_to(db, status, "complete").retagged == 0


def test_activation_returns_to_cleanup_if_a_retired_namespace_reappears(store):
    _, db = root_seed(store)
    status = advance_to(db, begin(db), "activate")
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"name", b"late-orphan"))
    store.set(root, b"raw")
    status = db.advance_migration(status.migration_id, Product, Relaxed)
    assert status.phase == "cleanup" and store.get(GATE) is not None
    advance_to(db, status, "complete")
    assert store.get(root) is None


def test_missing_source_claim_fails_validation_and_allows_safe_abort(store):
    old, db = root_seed(store)
    root = Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b'v:"s:private-A"'))
    store.delete_tree(root)
    status = begin(db)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Product, Relaxed)
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)
    assert old.get(Product, "P0").sku == "private-A"


@pytest.mark.parametrize("phase", ["validate", "backfill"])
def test_abort_before_updates_preserves_source_indexes_and_constraints(store, phase):
    old, db = root_seed(store)
    before = data_nodes(store)
    status = advance_to(db, begin(db), phase)
    db.abort_migration(status.migration_id)
    assert data_nodes(store) == before and old.get(Product, "P0").name == "Main"


def test_retirement_plan_has_only_explicit_source_owned_index_namespaces(store):
    _, db = root_seed(store)
    roots = retired_roots(Registry(Product).spec(Product), db.plan_migration(Relaxed))
    assert {root.subscripts[1] for root in roots} == {b"name", b"price", b"sku"}
    assert Path("^YHMFIndex", (b"YHMFTestProduct", b"sku")) not in roots


def test_mixed_scalar_addition_and_index_removal_uses_the_same_atomic_batches(store):
    _, _ = root_seed(store)

    class Mixed(Relaxed):
        added: str = Field(default="ready", index=True)

    db = Database(store, Registry(Mixed))
    status = begin(db, Product, Mixed)
    advance_to(db, status, "complete", Product, Mixed)
    assert db.get(Mixed, "P0").added == "ready"
    assert len(db.query(Mixed).where(added="ready").all()) == 3
    assert db.check_indexes(Mixed).issues == ()


def test_reference_index_removal_stays_outside_scalar_migration_scope(store):
    class Related(Model):
        __global__ = Target.__global__
        id: int = Field(primary_key=True)

    class Source(Model):
        __global__ = Product.__global__
        id: int = Field(primary_key=True)
        target: Reference[Related] = Field(index=True)

    class NoIndex(Source):
        target: Reference[Related]

    old = Database(store, Registry(Related, Source))
    old.insert(Related(id=1))
    old.insert(Source(id=1, target=Reference(key=1)))
    db = Database(store, Registry(Related, NoIndex))
    plan = db.plan_migration(NoIndex)
    assert not plan.execution_supported
    with pytest.raises(MigrationError):
        db.begin_migration(Source, NoIndex, plan=plan)
    assert store.get(GATE) is None


@pytest.mark.parametrize("raw", [b"01", b"-1", b"1.0", b"not-a-count"])
def test_corrupt_cleanup_count_cannot_advance_a_job(store, raw):
    _, db = root_seed(store)
    status = advance_to(db, begin(db), "cleanup")
    root = job_path(status.migration_id).child(b"cleaned_nodes")
    store.set(root, raw)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Product, Relaxed)
    assert db.migration_status(status.migration_id) == status
    store.delete_tree(root)
    advance_to(db, status, "complete")
