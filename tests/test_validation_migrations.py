# SPDX-License-Identifier: GPL-3.0-or-later
"""Validation-only migrations preserve field bytes and recover source access."""

import pytest
from pydantic import field_validator

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    NativeStore,
    Path,
    Registry,
)
from mccann_hub.yhmf.errors import MigrationError, SchemaMismatch
from mccann_hub.yhmf.maintenance import GATE
from tests.adversarial_support import dump
from tests.test_schema_contracts import seed_legacy


class Before(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    amount: int = Field(default=2, le=5, index=True)


def finish(store, source, target, status):
    phases = []
    for _ in range(100):
        phases.append(status.phase)
        if status.phase == "complete":
            return status, phases
        # A fresh client on every call also exercises durable recovery bindings.
        db = Database(store, Registry(target))
        status = db.advance_migration(status.migration_id, source, target, batch_size=1)
    raise AssertionError("Migration failed to complete")


@pytest.mark.parametrize("kind", ["revision", "validator", "constraint", "default", "nullable"])
def test_validation_only_changes_preserve_values_and_skip_backfill(store, monkeypatch, kind):
    class Revision(Before):
        __schema_version__ = 2

    class Validator(Revision):
        @field_validator("amount")
        @classmethod
        def positive(cls, value):
            assert value > 0
            return value

    class Constraint(Before):
        amount: int = Field(default=2, le=10, index=True)

    class Default(Before):
        amount: int = Field(default=3, le=5, index=True)

    class Nullable(Before):
        amount: int | None = Field(default=2, le=5, index=True)

    target = {
        "revision": Revision,
        "validator": Validator,
        "constraint": Constraint,
        "default": Default,
        "nullable": Nullable,
    }[kind]
    original = Database(store, Registry(Before))
    original.insert(Before(id=1))
    original.insert(Before(id=2, amount=4))
    db = Database(store, Registry(target))
    plan = db.plan_migration(target)
    assert plan.execution_supported and plan.validation_only
    assert plan.execution_blockers == ()
    status = db.begin_migration(Before, target, plan=plan)
    writes = []
    real_set, real_delete = store.set, store.delete_tree

    def set_node(path, value):
        writes.append(path)
        return real_set(path, value)

    def delete_node(path):
        writes.append(path)
        return real_delete(path)

    monkeypatch.setattr(store, "set", set_node)
    monkeypatch.setattr(store, "delete_tree", delete_node)
    status, phases = finish(store, Before, target, status)
    assert "backfill" not in phases
    assert (status.validated, status.backfilled, status.verified, status.retagged) == (2, 2, 2, 2)
    assert not any(path.global_name in {"^YHMFIndex", "^YHMFUnique", "^YHMFRef"} for path in writes)
    assert all(b"%m" in path.subscripts for path in writes if path.global_name == Before.__global__)
    assert db.get(target, 1).amount == 2 and db.get(target, 2).amount == 4
    assert db.check_indexes(target).issues == ()
    assert db.inspect_schema(target).status == "matching"
    with pytest.raises(SchemaMismatch):
        original.get(Before, 1)
    assert db.advance_migration(status.migration_id, Before, target) == status


@pytest.mark.parametrize("kind", ["constraint", "validator", "normalization", "nullability"])
def test_rejected_validation_only_change_aborts_and_restores_source(store, kind):
    class Source(Model):
        __global__ = Before.__global__
        id: int = Field(primary_key=True)
        amount: int | None = Field(default=None, index=True)

    class Constraint(Source):
        amount: int | None = Field(default=None, le=3, index=True)

    class Validator(Source):
        __schema_version__ = 2

        @field_validator("amount")
        @classmethod
        def reject(cls, value):
            if value == 4:
                raise ValueError("private-record-value")
            return value

    class Normalizing(Source):
        __schema_version__ = 2

        @field_validator("amount")
        @classmethod
        def normalize(cls, value):
            return value + 1 if value == 4 else value

    class Required(Source):
        amount: int = Field(index=True)

    target = {
        "constraint": Constraint,
        "validator": Validator,
        "normalization": Normalizing,
        "nullability": Required,
    }[kind]
    old = Database(store, Registry(Source))
    old.insert(Source(id=1, amount=2))
    old.insert(Source(id=2, amount=None if kind == "nullability" else 4))
    root = Path(Source.__global__, (b"1",))
    metadata = store.get(root.child(b"%m", b"fingerprint"))
    db = Database(store, Registry(target))
    status = db.begin_migration(Source, target, plan=db.plan_migration(target))
    status = db.advance_migration(status.migration_id, Source, target, batch_size=1)
    assert status.validated == 1
    with pytest.raises(MigrationError) as error:
        db.advance_migration(status.migration_id, Source, target, batch_size=1)
    assert "private-record-value" not in str(error.value)
    assert db.migration_status(status.migration_id).phase == "aborted"
    assert store.get(GATE) is None
    assert store.get(root.child(b"%m", b"fingerprint")) == metadata
    assert old.get(Source, 1).amount == 2
    assert old.get(Source, 2).amount == (None if kind == "nullability" else 4)
    assert old.check_indexes(Source).issues == ()


def test_owned_revision_and_constraint_changes_are_validated(store):
    class Child(Model):
        id: str = Field(primary_key=True)
        amount: int = Field(ge=0)

    class Parent(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    class NewChild(Child):
        __schema_version__ = 2
        amount: int = Field(ge=0, le=10)

    class Target(Parent):
        children: Children[NewChild] = ChildField(key="id")

    old = Database(store, Registry(Parent))
    old.insert(Parent(id=1, children={"C": Child(id="C", amount=4)}))
    db = Database(store, Registry(Target))
    plan = db.plan_migration(Target)
    assert plan.execution_supported and plan.validation_only
    status = db.begin_migration(Parent, Target, plan=plan)
    status, phases = finish(store, Parent, Target, status)
    assert status.phase == "complete" and "backfill" not in phases
    assert db.get(Target, 1).children["C"].amount == 4


def test_unsupported_plan_explains_blockers_without_record_values(store):
    old = Database(store, Registry(Before))
    old.insert(Before(id=1))

    class Target(Before):
        extra: str

    plan = Database(store, Registry(Target)).plan_migration(Target)
    assert not plan.execution_supported and not plan.validation_only
    assert "unsupported_change:field_added:extra" in plan.execution_blockers
    assert plan.to_dict()["execution_blockers"] == list(plan.execution_blockers)


def test_validation_only_migration_explicitly_upgrades_a_legacy_binding(store):
    class Target(Before):
        __schema_version__ = 2

    seed_legacy(store, Before(id=1))
    db = Database(store, Registry(Target))
    assert db.inspect_schema(Target).installed.format_version == 1
    status = db.begin_migration(Before, Target, plan=db.plan_migration(Target))
    status, phases = finish(store, Before, Target, status)
    assert status.phase == "complete" and "backfill" not in phases
    assert db.inspect_schema(Target).installed.format_version == 2
    assert db.get(Target, 1).amount == 2


@pytest.mark.parametrize("phase", ["retag", "activate"])
def test_validation_only_write_faults_roll_back_and_resume(store, monkeypatch, phase):
    class Target(Before):
        __schema_version__ = 2

    old = Database(store, Registry(Before))
    old.insert(Before(id=1))
    db = Database(store, Registry(Target))
    status = db.begin_migration(Before, Target, plan=db.plan_migration(Target))
    for _ in range(30):
        if status.phase == phase:
            break
        status = db.advance_migration(status.migration_id, Before, Target, batch_size=1)
    assert status.phase == phase
    roots = (Before.__global__, "^YHMFSchema", "^YHMFMigration", "^YHMFIndex")
    before, real_set, tripped = dump(store, roots), store.set, []

    def fail_after_write(path, value):
        real_set(path, value)
        tripped.append(path)
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", fail_after_write)
        with pytest.raises(RuntimeError, match="injected"):
            db.advance_migration(status.migration_id, Before, Target, batch_size=1)
    assert tripped and dump(store, roots) == before
    assert db.migration_status(status.migration_id) == status
    status, _ = finish(store, Before, Target, status)
    assert status.phase == "complete" and db.get(Target, 1).amount == 2


def test_autoabort_itself_is_transactional_and_can_be_retried(store, monkeypatch):
    class Invalid(Before):
        amount: int = Field(default=2, le=1, index=True)

    old = Database(store, Registry(Before))
    old.insert(Before(id=1))
    db = Database(store, Registry(Invalid))
    status = db.begin_migration(Before, Invalid, plan=db.plan_migration(Invalid))
    roots = (Before.__global__, "^YHMFSchema", "^YHMFMigration")
    before, real_set = dump(store, roots), store.set

    def fail_after_restore(path, value):
        real_set(path, value)
        raise RuntimeError("interrupted restoration")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", fail_after_restore)
        with pytest.raises(RuntimeError, match="interrupted"):
            db.advance_migration(status.migration_id, Before, Invalid)
    assert dump(store, roots) == before
    assert db.migration_status(status.migration_id) == status
    with pytest.raises(MigrationError):
        db.advance_migration(status.migration_id, Before, Invalid)
    assert db.migration_status(status.migration_id).phase == "aborted"
    assert old.get(Before, 1).amount == 2


@pytest.mark.native
def test_native_restart_during_automatic_source_restoration(store, monkeypatch):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native transaction restarts")
    yottadb = pytest.importorskip("yottadb")

    class Invalid(Before):
        amount: int = Field(default=2, le=1, index=True)

    old = Database(store, Registry(Before))
    old.insert(Before(id=1))
    db = Database(store, Registry(Invalid))
    status = db.begin_migration(Before, Invalid, plan=db.plan_migration(Invalid))
    real_set, attempts = store.set, []

    def restart_after_restore(path, value):
        real_set(path, value)
        if path.global_name == "^YHMFSchema" and len(path.subscripts) == 1:
            attempts.append(path)
            if len(attempts) == 1:
                raise yottadb.YDBTPRestart()

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", restart_after_restore)
        with pytest.raises(MigrationError):
            db.advance_migration(status.migration_id, Before, Invalid)
    assert len(attempts) == 2
    assert db.migration_status(status.migration_id).phase == "aborted"
    assert store.get(GATE) is None and old.get(Before, 1).amount == 2
