# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Field, Model, Registry
from mccann_hub.yhmf.errors import MigrationError, TransactionError
from mccann_hub.yhmf.maintenance import GATE, job_path
from tests.adversarial_support import dump


class Source(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    value: str


class Target(Source):
    value: str = Field(unique=True)


def job(store, *, aborted=False, rebuild=False):
    original = Database(store, Registry(Target if rebuild else Source))
    for key in range(3):
        original.insert((Target if rebuild else Source)(id=key, value=f"private-reservation-{key}"))
    db = Database(store, Registry(Target))
    if rebuild:
        status = db.begin_index_rebuild(Target, plan=db.plan_index_rebuild(Target))

        def advance():
            return db.advance_index_rebuild(status.migration_id, Target, batch_size=1)
    else:
        status = db.begin_migration(Source, Target, plan=db.plan_migration(Target))

        def advance():
            return db.advance_migration(status.migration_id, Source, Target, batch_size=1)

    if aborted:
        status = advance()  # Commit a uniqueness reservation without application writes.
        return db, db.abort_migration(status.migration_id)
    for _ in range(100):
        if status.phase == "complete":
            return db, status
        status = advance()
    raise AssertionError("Maintenance job failed to complete")


@pytest.mark.parametrize("aborted,rebuild", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("batch_size", [1, 3])
def test_pruning_is_bounded_source_free_resumable_and_removes_private_reservations(
    store, aborted, rebuild, batch_size
):
    db, status = job(store, aborted=aborted, rebuild=rebuild)
    data_roots = (Source.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef")
    before = dump(store, data_roots)
    artifact = dump(store, ("^YHMFMigration",))
    assert any(b"private-reservation" in value for value in artifact.values())
    for _ in range(100):
        # No source or target Python declaration is needed to continue pruning.
        client = Database(store, Registry())
        old_nodes = dump(store, ("^YHMFMigration",))
        complete = client.prune_migration(status.migration_id, batch_size=batch_size)
        remaining = dump(store, ("^YHMFMigration",))
        assert 1 <= len(old_nodes.keys() - remaining.keys()) <= batch_size
        assert dump(store, data_roots) == before
        if complete:
            break
        assert client.migration_status(status.migration_id).pruning
    else:
        raise AssertionError("Pruning failed to make bounded progress")
    assert not store.children(job_path(status.migration_id), limit=1)
    assert store.get(job_path(status.migration_id)) is None
    assert not any(b"private-reservation" in value for value in remaining.values())
    assert client.prune_migration(status.migration_id, batch_size=1)
    with pytest.raises(MigrationError, match="does not exist"):
        db.migration_status(status.migration_id)


def test_active_job_cannot_be_pruned(store):
    Database(store, Registry(Source)).insert(Source(id=1, value="value"))
    db = Database(store, Registry(Target))
    status = db.begin_migration(Source, Target, plan=db.plan_migration(Target))
    before = dump(store, (Source.__global__, "^YHMFMigration", "^YHMFSchema"))
    with pytest.raises(MigrationError, match="active"):
        db.prune_migration(status.migration_id)
    assert dump(store, (Source.__global__, "^YHMFMigration", "^YHMFSchema")) == before


@pytest.mark.parametrize("batch_size", [0, -1, 101, True, 1.5])
def test_invalid_prune_bounds_do_not_modify_jobs(store, batch_size):
    db, status = job(store)
    before = dump(store, ("^YHMFMigration",))
    with pytest.raises(MigrationError):
        db.prune_migration(status.migration_id, batch_size=batch_size)
    assert dump(store, ("^YHMFMigration",)) == before


def test_prune_failure_rolls_back_the_checkpoint_and_deletions(store, monkeypatch):
    db, status = job(store)
    before, delete = dump(store, ("^YHMFMigration",)), store.delete_tree

    def interrupt(path):
        delete(path)
        raise RuntimeError("injected pruning interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "delete_tree", interrupt)
        with pytest.raises(RuntimeError, match="injected"):
            db.prune_migration(status.migration_id, batch_size=2)
    assert dump(store, ("^YHMFMigration",)) == before
    assert db.prune_migration(status.migration_id, batch_size=100)


@pytest.mark.parametrize("rebuild", [False, True])
def test_a_partially_pruned_job_cannot_resume_or_abort_maintenance(store, rebuild):
    db, status = job(store, rebuild=rebuild)
    assert not db.prune_migration(status.migration_id, batch_size=1)
    with pytest.raises(MigrationError, match="being pruned"):
        if rebuild:
            db.advance_index_rebuild(status.migration_id, Target)
        else:
            db.advance_migration(status.migration_id, Source, Target)
    with pytest.raises(MigrationError, match="being pruned"):
        db.abort_migration(status.migration_id)


def test_unrelated_terminal_job_can_be_pruned_during_active_maintenance(store):
    db, terminal = job(store)

    class Next(Target):
        __schema_version__ = 2

    active = Database(store, Registry(Next))
    status = active.begin_migration(Target, Next, plan=active.plan_migration(Next))
    before = dump(store, ("^YHMFSchema", Source.__global__))
    assert db.prune_migration(terminal.migration_id, batch_size=100)
    assert store.get(GATE) == status.migration_id.encode("ascii")
    assert active.migration_status(status.migration_id) == status
    assert dump(store, ("^YHMFSchema", Source.__global__)) == before


def test_pruning_requires_a_standalone_active_handle(store):
    db, status = job(store)
    with pytest.raises(TransactionError):
        db.atomic(lambda tx: tx.prune_migration(status.migration_id))
    tx = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError):
        tx.prune_migration(status.migration_id)
