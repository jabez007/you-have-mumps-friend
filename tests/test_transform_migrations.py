# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, Field, NativeStore, Reference, Registry, Transform
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    MigrationError,
    SchemaMismatch,
    StorageError,
    UniqueViolation,
)
from mccann_hub.yhmf.maintenance import job_path
from tests.adversarial_support import dump
from tests.test_native_schema_safety import ready
from tests.test_schema_contracts import seed_legacy
from tests.transform_models import KINDS, RELATED, Destination, OtherDestination, Source, case

ROOTS = (
    Source.__global__,
    Destination.__global__,
    OtherDestination.__global__,
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
    "^YHMFMigration",
    "^YHMFSchema",
)


def setup(store, kind, *, legacy=False):
    old = Database(store, Registry(*RELATED, Source))
    old.insert(Destination(id="T"))
    old.insert(OtherDestination(id="T"))
    for number in range(3):
        record = Source(
            id=str(number),
            name=f"N{number}",
            serial=f"S{number}",
            amount=number + 1,
            related=Reference[Destination](key="T"),
        )
        if legacy:
            seed_legacy(store, record)
            from mccann_hub.yhmf import references

            spec = old.registry.spec(Source)
            links = references.links(old.registry, spec, spec.path(record.id), record)
            store.atomic(lambda links=links: references.replace(store, frozenset(), links))
        else:
            old.insert(record)
    target, transform = case(kind)
    db = Database(store, Registry(*RELATED, target))
    plan = db.plan_migration(target, transform=transform)
    assert plan.execution_supported and plan.requires_manual_review and plan.destructive
    assert plan.to_dict()["abort_boundary"] == "first_committed_index_delete_or_record_rewrite"
    status = db.begin_migration(Source, target, plan=plan, transform=transform)
    return db, target, transform, status


def to_phase(db, target, transform, status, phase):
    for _ in range(200):
        if status.phase == phase:
            return status
        # New handles/registries on each checkpoint model a restarted application.
        db = Database(db.store, Registry(*RELATED, target))
        status = db.advance_migration(
            status.migration_id, Source, target, transform=transform, batch_size=1
        )
    raise AssertionError("Transform did not reach its phase")


def verify(db, target, kind):
    for number in range(3):
        record = db.get(target, str(number))
        if kind == "rename":
            assert record.label == f"N{number}"
            assert db.store.get(db.registry.spec(target).path(str(number)).child(b"name")) is None
        elif kind == "remove":
            assert not hasattr(record, "name")
        elif kind == "type":
            assert record.amount == str(number + 1)
        elif kind == "factory":
            assert record.token == "reproducible"
        elif kind == "owned":
            assert record.detail.text == f"N{number}" and record.samples[0].value == number + 1
        elif kind == "reference":
            assert db.resolve(record.related).id == "T"
            assert isinstance(record.related, Reference[OtherDestination])
        elif kind == "swap":
            assert record.serial == f"S{(number + 1) % 3}"
    assert db.check_indexes(target).issues == ()
    assert db.inspect_schema(target).status == "matching"
    if kind == "reference":
        assert db.delete(Destination, "T")
        assert len(db.referrers(Reference[OtherDestination](key="T"), source=target)) == 3


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "phase", ["validate", "clear_indexes", "backfill", "verify", "retag", "activate"]
)
def test_each_transform_rolls_back_an_interrupted_batch_and_resumes(
    store, monkeypatch, kind, phase
):
    db, target, transform, status = setup(store, kind)
    status = to_phase(db, target, transform, status, phase)
    if phase in {"validate", "backfill", "verify", "retag"}:
        status = db.advance_migration(
            status.migration_id, Source, target, transform=transform, batch_size=1
        )
    before, original = dump(store, ROOTS), store.set

    def interrupt(path, value):
        original(path, value)
        raise RuntimeError("injected transform interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", interrupt)
        with pytest.raises(RuntimeError, match="interruption"):
            db.advance_migration(
                status.migration_id, Source, target, transform=transform, batch_size=1
            )
    assert dump(store, ROOTS) == before
    assert db.migration_status(status.migration_id) == status
    status = to_phase(db, target, transform, status, "complete")
    assert (status.validated, status.backfilled, status.verified, status.retagged) == (3, 3, 3, 3)
    verify(db, target, kind)
    assert db.advance_migration(status.migration_id, Source, target, transform=transform) == status
    with pytest.raises(SchemaMismatch):
        Database(store, Registry(*RELATED, Source)).get(Source, "0")


def test_transform_exception_rolls_back_its_own_database_writes(store):
    db, target, transform, status = setup(store, "rename")
    root = db.registry.spec(target).path("0")

    def bad(source):
        store.set(root.child(b"amount"), b"i:999")
        raise ValueError("private source data")

    before = dump(store, ROOTS)
    with pytest.raises(MigrationError) as error:
        db.advance_migration(
            status.migration_id, Source, target, transform=Transform(transform.name, bad)
        )
    assert "private source data" not in str(error.value)
    assert dump(store, ROOTS) == before
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)


def test_changed_transform_output_is_rejected_before_a_rewrite(store):
    db, target, transform, status = setup(store, "rename")
    status = to_phase(db, target, transform, status, "backfill")
    before = dump(store, ROOTS)

    def changed(source):
        result = transform.convert(source)
        result.label = "different"
        return result

    with pytest.raises(StorageError, match="changed since validation"):
        db.advance_migration(
            status.migration_id, Source, target, transform=Transform(transform.name, changed)
        )
    assert dump(store, ROOTS) == before
    to_phase(db, target, transform, status, "complete")


def test_transform_operation_and_record_digest_match_version_one_golden(store):
    db, target, transform, status = setup(store, "rename")
    db.advance_migration(status.migration_id, Source, target, transform=transform, batch_size=1)
    fixture = json.loads(
        (FilePath(__file__).with_name("fixtures") / "record-transform-v1.json").read_text()
    )
    root = job_path(status.migration_id)
    assert store.get(root.child(b"operation")) == canonical_json(fixture["operation"])
    path = root.child(b"records", hashlib.sha256(b"s:0").hexdigest().encode())
    assert store.get(path) == canonical_json(fixture["record"])


def test_transform_revision_wrong_executor_and_review_drift_are_rejected(store):
    db, target, transform, status = setup(store, "rename")
    before = dump(store, ROOTS)
    with pytest.raises(MigrationError, match="executor"):
        db.advance_migration(status.migration_id, Source, target)
    with pytest.raises(MigrationError, match="not an index rebuild"):
        db.advance_index_rebuild(status.migration_id, target)
    with pytest.raises(MigrationError, match="revision"):
        db.advance_migration(
            status.migration_id, Source, target, transform=Transform("other", transform.convert)
        )
    assert dump(store, ROOTS) == before
    db.abort_migration(status.migration_id)
    plan = db.plan_migration(target, transform=transform)
    with pytest.raises(MigrationError, match="Supply the transform"):
        db.begin_migration(Source, target, plan=plan)
    with pytest.raises(MigrationError, match="Reviewed"):
        db.begin_migration(Source, target, plan=replace(plan, steps=()), transform=transform)


def test_duplicate_final_unique_values_fail_in_validation_and_can_abort(store):
    db, target, transform, status = setup(store, "rename")

    def duplicate(source):
        result = transform.convert(source)
        result.serial = "same"
        return result

    attempt = Transform(transform.name, duplicate)
    first = db.advance_migration(
        status.migration_id, Source, target, transform=attempt, batch_size=1
    )
    with pytest.raises(UniqueViolation):
        db.advance_migration(status.migration_id, Source, target, transform=attempt, batch_size=1)
    assert db.migration_status(status.migration_id) == first
    db.abort_migration(status.migration_id)
    assert Database(store, Registry(*RELATED, Source)).check_indexes(Source).issues == ()


def test_first_index_delete_is_the_reviewed_abort_boundary(store):
    db, target, transform, status = setup(store, "rename")
    status = to_phase(db, target, transform, status, "clear_indexes")
    db.advance_migration(status.migration_id, Source, target, transform=transform, batch_size=1)
    with pytest.raises(MigrationError, match="cannot be aborted"):
        db.abort_migration(status.migration_id)
    to_phase(db, target, transform, status, "complete")


def test_legacy_source_transform_and_terminal_pruning_preserve_application_data(store):
    db, target, transform, status = setup(store, "rename", legacy=True)
    to_phase(db, target, transform, status, "complete")
    verify(db, target, "rename")
    roots = (Source.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef", "^YHMFSchema")
    before = dump(store, roots)
    cleaner = Database(store, Registry())
    while not cleaner.prune_migration(status.migration_id, batch_size=1):
        pass
    assert dump(store, roots) == before
    assert store.get(job_path(status.migration_id)) is None


@pytest.mark.parametrize("phase", ["backfill", "verify", "retag"])
def test_corrupt_transform_record_digest_keeps_maintenance_held(store, phase):
    db, target, transform, status = setup(store, "rename")
    status = to_phase(db, target, transform, status, phase)
    root = job_path(status.migration_id).child(b"records")
    key = store.children(root, limit=1)[0]
    store.set(root.child(key), b"corrupt")
    before = dump(store, ROOTS)
    with pytest.raises(StorageError):
        db.advance_migration(status.migration_id, Source, target, transform=transform)
    assert dump(store, ROOTS) == before
    with pytest.raises(MaintenanceActive):
        db.get(target, "0")


def test_transform_preserves_root_identity_and_requires_the_exact_target_type(store):
    db, target, transform, status = setup(store, "rename")
    before = dump(store, ROOTS)

    def wrong_key(source):
        result = transform.convert(source)
        result.id = "other"
        return result

    for convert in (wrong_key, lambda source: source):
        with pytest.raises(MigrationError):
            db.advance_migration(
                status.migration_id, Source, target, transform=Transform(transform.name, convert)
            )
        assert dump(store, ROOTS) == before


def test_root_key_type_changes_are_not_reviewed_as_supported(store):
    old = Database(store, Registry(*RELATED, Source))
    old.insert(Source(id="0", name="N", serial="S"))

    class Numeric(Source):
        __schema_version__ = 2
        id: int = Field(primary_key=True)

    transform = Transform("key-change", lambda source: Numeric(**(source.model_dump() | {"id": 0})))
    db = Database(store, Registry(*RELATED, Numeric))
    plan = db.plan_migration(Numeric, transform=transform)
    assert not plan.execution_supported
    assert "unsupported_transform:primary_key_type_changed" in plan.execution_blockers
    with pytest.raises(MigrationError):
        db.begin_migration(Source, Numeric, plan=plan, transform=transform)


@pytest.mark.native
def test_native_transform_restart_reloads_source_data_and_rolls_back_reverse_edges(
    store, monkeypatch
):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restarts")
    yottadb = pytest.importorskip("yottadb")
    db, target, transform, status = setup(store, "reference")
    status = to_phase(db, target, transform, status, "backfill")
    original, attempts = store.set, []

    def restart_after_checkpoint(path, value):
        original(path, value)
        if path == job_path(status.migration_id):
            attempts.append(value)
            if len(attempts) == 1:
                raise yottadb.YDBTPRestart()

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", restart_after_checkpoint)
        updated = db.advance_migration(
            status.migration_id, Source, target, transform=transform, batch_size=1
        )
    assert attempts[0] == attempts[1] and updated.backfilled == 1
    to_phase(db, target, transform, updated, "complete")
    verify(db, target, "reference")


@pytest.mark.native
@pytest.mark.adversarial
def test_native_stale_transform_checkpoint_retries_after_a_competing_rewrite(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires independent native processes")
    import json

    db, target, transform, status = setup(store, "rename")
    status = to_phase(db, target, transform, status, "backfill")
    worker = FilePath(__file__).with_name("native_transforms.py")
    process = subprocess.Popen(
        [sys.executable, str(worker), status.migration_id, "rename"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(process)
        first = db.advance_migration(
            status.migration_id, Source, target, transform=transform, batch_size=1
        )
        assert first.backfilled == 1
        stdout, stderr = process.communicate("go\n", timeout=30)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["backfilled"] == 2
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    to_phase(db, target, transform, db.migration_status(status.migration_id), "complete")
    verify(db, target, "rename")
