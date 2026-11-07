# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Field, Model, Reference, Registry
from mccann_hub.yhmf.errors import MaintenanceActive, ReferenceViolation, StorageError
from mccann_hub.yhmf.maintenance import GATE, job_path, require_available
from tests.adversarial_support import dump


class Other(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)
    value: int = Field(default=0, index=True)


class Source(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    related: Reference[Other] | None = None


class Target(Source):
    __schema_version__ = 2
    note: str = "new"


class Referrer(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    target: Reference[Source] | None = None
    label: str = "old"


@pytest.fixture
def migration(store):
    old = Database(store, Registry(Other, Source, Referrer))
    old.insert(Other(id="B"))
    old.insert(Source(id="A", related=Reference[Other](key="B")))
    old.insert(Referrer(id="R", target=Reference[Source](key="A")))
    manager = Database(store, Registry(Other, Target))
    job = manager.begin_migration(Source, Target, plan=manager.plan_migration(Target))
    return old, manager, job


def test_unrelated_crud_queries_and_resolution_work_under_maintenance(migration):
    old, manager, job = migration
    old.insert(Other(id="C", value=1))
    old.patch(Other, "C", changes={"value": 2})
    assert old.get(Other, "C").value == 2
    assert [value.id for value in old.query(Other).where(value=2).all()] == ["C"]
    assert [value.id for value in old.scan(Other)] == ["B", "C"]
    assert old.resolve(Reference[Other](key="C")).value == 2
    assert old.resolve_many([]) == []
    assert old.check_indexes(Other).issues == ()
    old.save(Other(id="C", value=3))
    assert old.delete(Other, "C")
    assert manager.migration_status(job.migration_id) == job
    with pytest.raises(MaintenanceActive):
        old.get(Source, "A")


def test_reference_edges_preserve_the_existing_target_and_restrict_checks(migration):
    old, manager, job = migration
    assert old.get(Referrer, "R").target == Reference[Source](key="A")
    old.patch(Referrer, "R", changes={"label": "independent scalar"})
    with pytest.raises(MaintenanceActive):
        old.save(old.get(Referrer, "R"))
    with pytest.raises(MaintenanceActive):
        old.insert(Referrer(id="new", target=Reference[Source](key="A")))
    with pytest.raises(MaintenanceActive):
        old.resolve(Reference[Source](key="A"))
    with pytest.raises(ReferenceViolation):
        old.delete(Other, "B")
    old.patch(Referrer, "R", changes={"target": None})
    assert old.delete(Referrer, "R")
    manager.abort_migration(job.migration_id)
    assert old.delete(Source, "A")
    assert old.delete(Other, "B")


def test_cross_model_failure_rolls_back_unrelated_writes(migration):
    old, _, _ = migration
    before = dump(old.store, (Other.__global__, "^YHMFIndex"))

    def update(tx):
        tx.patch(Other, "B", changes={"value": 99})
        tx.get(Source, "A")

    with pytest.raises(MaintenanceActive):
        old.atomic(update)
    assert dump(old.store, (Other.__global__, "^YHMFIndex")) == before


def test_second_unrelated_maintenance_job_is_still_refused(migration):
    old, manager, job = migration

    class OtherTarget(Other):
        __schema_version__ = 2

    second = Database(old.store, Registry(OtherTarget))
    with pytest.raises(MaintenanceActive):
        second.begin_migration(Other, OtherTarget, plan=second.plan_migration(OtherTarget))
    with pytest.raises(MaintenanceActive):
        old.begin_index_rebuild(Other, plan=old.plan_index_rebuild(Other))
    assert manager.migration_status(job.migration_id) == job


@pytest.mark.parametrize("damage", ["missing", "corrupt", "barrier"])
def test_untrusted_active_job_cannot_allow_unrelated_operations(migration, damage):
    old, manager, job = migration
    if damage == "missing":
        old.store.delete_tree(job_path(job.migration_id))
    elif damage == "corrupt":
        old.store.set(job_path(job.migration_id), b"not-json")
    else:
        from mccann_hub.yhmf.schemas import schema_path

        old.store.set(schema_path(Source.__global__), b"corrupt")
    with pytest.raises((StorageError, MaintenanceActive)):
        old.get(Other, "B")


def test_legacy_global_gate_and_barrier_remain_effective(migration):
    old, _, _ = migration
    with pytest.raises(MaintenanceActive):
        old.store.atomic(lambda: require_available(old.store))
    assert old.store.get(GATE) is not None


def test_index_rebuild_uses_the_same_model_scoped_rule(store):
    db = Database(store, Registry(Other, Source))
    db.insert(Other(id="B"))
    db.insert(Source(id="A"))
    job = db.begin_index_rebuild(Other, plan=db.plan_index_rebuild(Other))
    assert db.get(Source, "A").id == "A"
    with pytest.raises(MaintenanceActive):
        db.get(Other, "B")
    db.abort_migration(job.migration_id)
