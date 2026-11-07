# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest

from mccann_hub.yhmf import Database, Field, Model, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    MigrationError,
    ReferenceViolation,
    StorageError,
    UniqueViolation,
)
from mccann_hub.yhmf.maintenance import GATE, job_path
from mccann_hub.yhmf.references import reference_root
from mccann_hub.yhmf.schemas import schema_path
from mccann_hub.yhmf.storage import subtree_nodes
from tests.test_adoption import Lot, Product, raw
from tests.test_partial_mutations import ObservedStore


def seed(store, count=3, *, cycle=False):
    registry = Registry(Product)
    for i in range(count):
        key = f"P{i:04}"
        related = Reference[Product](key=f"P{(i + 1) % count:04}") if cycle else None
        raw(
            store,
            registry,
            Product(
                id=key,
                name=f"Part{i}",
                related=related,
                lots={"L": Lot(id="L", quantity=Decimal(f"{i}.50"))},
            ),
        )
    return Database(store, registry)


def reopen(store):
    return Database(store, Registry(Product))


def phase(store, status, wanted, *, batch_size=25):
    for _ in range(300):
        if status.phase == wanted:
            return status
        status = reopen(store).advance_adoption(status.migration_id, Product, batch_size=batch_size)
    raise AssertionError("Adoption did not reach the requested phase")


def fields(store):
    return {
        path: value
        for path, value in subtree_nodes(store, Path(Product.__global__), limit=10000).items()
        if path.subscripts[1] != b"%m"
    }


def test_more_than_one_hundred_owned_records_resume_and_activate_without_field_rewrites(store):
    observed = ObservedStore(store)
    db = seed(observed, 203, cycle=True)
    before = fields(store)
    with pytest.raises(StorageError, match="at most 100"):
        db.adopt(Product)
    status = db.begin_adoption(Product)
    assert status.source_fingerprint is None and status.phase == "validate"
    inspection = db.inspect_schema(Product)
    assert inspection.installed is None and inspection.migration_id == status.migration_id
    assert store.get(schema_path(Product.__global__).child(b"snapshot")) is None
    with pytest.raises(MaintenanceActive):
        db.get(Product, "P0000")
    with pytest.raises(MaintenanceActive):
        db.insert(Product(id="new", name="New"))
    observed.operations.clear()
    status = phase(observed, status, "complete", batch_size=17)
    assert (status.validated, status.backfilled, status.verified, status.retagged) == (
        203,
        203,
        203,
        0,
    )
    assert fields(store) == before
    assert not any(
        op in {"set", "delete"}
        and path.global_name == Product.__global__
        and (len(path.subscripts) < 2 or path.subscripts[1] != b"%m")
        for op, path in observed.operations
    )
    assert reopen(store).get(Product, "P0000").lots["L"].quantity == Decimal("0.50")
    assert reopen(store).check_indexes(Product, limit=1000).issues == ()
    assert store.get(reference_root(db.registry.spec(Product).path("P0000"))) == b"1"
    assert store.get(GATE) is None
    assert reopen(store).inspect_schema(Product).status == "matching"
    assert reopen(store).advance_adoption(status.migration_id, Product) == status
    with pytest.raises(ReferenceViolation):
        reopen(store).delete(Product, "P0000")
    with pytest.raises(MigrationError, match="cannot be aborted"):
        db.abort_migration(status.migration_id)


def test_early_abort_restores_absent_metadata_and_can_be_pruned_in_bounded_batches(store):
    db = seed(store)
    before = fields(store)
    status = db.begin_adoption(Product)
    status = db.advance_adoption(status.migration_id, Product, batch_size=1)
    assert status.validated == 1 and status.backfilled == 0
    status = db.abort_migration(status.migration_id)
    assert status.phase == "aborted" and db.abort_migration(status.migration_id) == status
    assert store.get(schema_path(Product.__global__)) is None
    assert store.children(schema_path(Product.__global__), limit=1) == ()
    assert fields(store) == before and store.get(GATE) is None
    assert db.inspect_schema(Product).installed is None
    for _ in range(100):
        if db.prune_migration(status.migration_id, batch_size=1):
            break
    assert store.get(job_path(status.migration_id)) is None
    assert db.adopt(Product) == 3


def test_committed_backfill_requires_resume_and_does_not_publish_an_installed_snapshot(store):
    db = seed(store)
    status = phase(store, db.begin_adoption(Product), "backfill", batch_size=1)
    status = db.advance_adoption(status.migration_id, Product, batch_size=1)
    assert status.backfilled == 1
    assert db.inspect_schema(Product).installed is None
    assert store.get(schema_path(Product.__global__).child(b"snapshot")) is None
    with pytest.raises(MigrationError, match="must be resumed"):
        db.abort_migration(status.migration_id)
    assert phase(store, status, "complete", batch_size=1).backfilled == 3


def test_uniqueness_is_validated_across_committed_batches_before_any_record_metadata(store):
    db = seed(store, 101)
    root = db.registry.spec(Product).path("P0100")
    store.set(root.child(b"name"), b"s:Part0")
    before = fields(store)
    status = db.begin_adoption(Product)
    status = db.advance_adoption(status.migration_id, Product, batch_size=100)
    assert status.validated == 100
    with pytest.raises(UniqueViolation):
        db.advance_adoption(status.migration_id, Product, batch_size=100)
    assert db.migration_status(status.migration_id) == status
    assert fields(store) == before
    assert not any(
        path.subscripts[1] == b"%m"
        for path in subtree_nodes(store, Path(Product.__global__), limit=10000)
    )
    assert store.children(Path("^YHMFUnique"), limit=1) == ()
    assert db.abort_migration(status.migration_id).phase == "aborted"


@pytest.mark.parametrize("damage", ["field_change", "extra", "missing", "identity", "reference"])
def test_failed_validation_or_changed_source_preserves_checkpoint_and_source(store, damage):
    db = seed(store)
    status = db.begin_adoption(Product)
    if damage == "field_change":
        status = phase(store, status, "backfill")
    root = db.registry.spec(Product).path("P0000")
    target = {
        "field_change": root.child(b"name"),
        "extra": root.child(b"unknown"),
        "missing": root.child(b"name"),
        "identity": root.child(b"id"),
        "reference": root.child(b"related"),
    }[damage]
    if damage == "missing":
        store.delete_tree(target)
    else:
        value = b"r:s:absent" if damage == "reference" else b"s:changed"
        store.set(target, value)
    before = subtree_nodes(store, Path(Product.__global__), limit=10000)
    with pytest.raises((StorageError, ValueError)):
        db.advance_adoption(status.migration_id, Product)
    assert db.migration_status(status.migration_id) == status
    assert subtree_nodes(store, Path(Product.__global__), limit=10000) == before
    assert db.abort_migration(status.migration_id).phase == "aborted"


def test_wrong_executors_and_revised_bindings_cannot_resume_adoption(store):
    db = seed(store)
    status = db.begin_adoption(Product)
    with pytest.raises(MigrationError, match="own maintenance executor"):
        db.advance_migration(status.migration_id, Product, Product)
    with pytest.raises(MigrationError, match="not an index rebuild"):
        db.advance_index_rebuild(status.migration_id, Product)

    class Changed(Model):
        __global__ = Product.__global__
        __schema_version__ = 2
        id: str = Field(primary_key=True)
        name: str

    with pytest.raises(MigrationError, match="saved target"):
        Database(store, Registry(Changed)).advance_adoption(status.migration_id, Changed)
    assert db.migration_status(status.migration_id) == status


@pytest.mark.parametrize("batch_size", [0, 101, True])
def test_adoption_batch_limits_are_checked_without_claiming_progress(store, batch_size):
    db = seed(store)
    status = db.begin_adoption(Product)
    with pytest.raises(MigrationError, match="batch size"):
        db.advance_adoption(status.migration_id, Product, batch_size=batch_size)
    assert db.migration_status(status.migration_id) == status


def test_adoption_keeps_unrelated_models_available(store):
    class Other(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)

    db = seed(store)
    status = db.begin_adoption(Product)
    other = Database(store, Registry(Other))
    other.insert(Other(id="safe"))
    assert other.get(Other, "safe").id == "safe"
    assert db.abort_migration(status.migration_id).phase == "aborted"


class InterruptedStore(ObservedStore):
    fail = None

    def set(self, path, value):
        super().set(path, value)
        if self.fail is not None and self.fail(path):
            self.fail = None
            raise RuntimeError("simulated interruption")


@pytest.mark.parametrize(
    "checkpoint", ["begin", "validate", "backfill", "metadata", "verify", "activate"]
)
def test_adoption_interruptions_rollback_partial_work_and_resume(store, checkpoint):
    interrupted = InterruptedStore(store)
    db = seed(interrupted)
    if checkpoint == "begin":
        interrupted.fail = lambda path: path.subscripts[-1:] == (b"target_snapshot",)
        with pytest.raises(RuntimeError, match="interruption"):
            db.begin_adoption(Product)
        assert store.get(GATE) is None and store.children(Path("^YHMFMigration"), limit=1) == ()
        assert db.inspect_schema(Product).installed is None
        return
    status = phase(
        interrupted,
        db.begin_adoption(Product),
        "backfill" if checkpoint == "metadata" else checkpoint,
        batch_size=1,
    )
    if checkpoint == "validate":
        interrupted.fail = (
            lambda path: b"unique" in path.subscripts and path.global_name == "^YHMFMigration"
        )
    elif checkpoint == "backfill":
        interrupted.fail = lambda path: path.global_name == "^YHMFUnique"
    elif checkpoint == "metadata":
        interrupted.fail = (
            lambda path: path.global_name == Product.__global__ and b"%m" in path.subscripts
        )
    elif checkpoint == "activate":
        interrupted.fail = lambda path: path == schema_path(Product.__global__).child(b"snapshot")
    else:
        interrupted.fail = lambda path: path == job_path(status.migration_id)
    before = {
        name: subtree_nodes(store, Path(name), limit=10000)
        for name in [
            Product.__global__,
            "^YHMFSchema",
            "^YHMFIndex",
            "^YHMFUnique",
            "^YHMFRef",
            "^YHMFMigration",
        ]
    }
    with pytest.raises(RuntimeError, match="interruption"):
        db.advance_adoption(status.migration_id, Product, batch_size=1)
    assert db.migration_status(status.migration_id) == status
    assert {name: subtree_nodes(store, Path(name), limit=10000) for name in before} == before
    assert phase(interrupted, status, "complete", batch_size=1).verified == 3
    assert db.check_indexes(Product).issues == ()


@pytest.mark.native
def test_native_adoption_backfill_restart_keeps_one_progress_and_reference_count(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native TP restart")
    yottadb = pytest.importorskip("yottadb")

    class RestartStore(ObservedStore):
        restart = False

        def set(self, path, value):
            super().set(path, value)
            if self.restart and path.global_name == "^YHMFUnique":
                self.restart = False
                raise yottadb.YDBTPRestart()

    restarted = RestartStore(store)
    db = seed(restarted, cycle=True)
    status = phase(restarted, db.begin_adoption(Product), "backfill", batch_size=1)
    restarted.restart = True
    status = db.advance_adoption(status.migration_id, Product, batch_size=1)
    assert status.backfilled == 1
    assert phase(restarted, status, "complete", batch_size=1).verified == 3
    assert db.check_indexes(Product).issues == ()
    for i in range(3):
        assert store.get(reference_root(db.registry.spec(Product).path(f"P{i:04}"))) == b"1"


@pytest.mark.native
@pytest.mark.parametrize(
    "operations", [("adopt", "adopt"), ("adopt", "insert"), ("insert", "adopt")]
)
def test_concurrent_adoption_and_schema_initialization_have_one_owner(store, operations):
    import subprocess
    import sys

    from tests.test_native_schema_safety import ready

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native multi-process transactions")
    workers = []
    try:
        for operation in operations:
            worker = subprocess.Popen(
                [sys.executable, "-m", "tests.native_adoption", operation],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(worker)
            ready(worker)
        results = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0, error
            results.append(output.strip())
        if operations[0] == "adopt":
            assert results[0].startswith("job:") and results[1] == "MaintenanceActive"
            job = results[0].removeprefix("job:")
            assert reopen(store).migration_status(job).source_fingerprint is None
            assert reopen(store).abort_migration(job).phase == "aborted"
            assert reopen(store).inspect_schema(Product).installed is None
        else:
            assert results == ["inserted", "MigrationError"]
            assert reopen(store).get(Product, "managed").name == "Managed"
            assert store.get(GATE) is None
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
