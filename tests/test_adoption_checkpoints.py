# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from mccann_hub.yhmf import Database, Field, MigrationStatus, Model, Path, Registry
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import MaintenanceActive, MigrationError, StorageError
from mccann_hub.yhmf.maintenance import GATE, job_path, load_status, marker, save_status
from mccann_hub.yhmf.migration_jobs import saved_snapshots
from mccann_hub.yhmf.schemas import schema_path

JOB = "a" * 32
TARGET = "b" * 64
OPERATION = "c" * 64


def test_existing_checkpoint_bytes_keep_format_one(store):
    status = MigrationStatus(JOB, "^YHMFTestProduct", "d" * 64, TARGET)
    save_status(store, status)
    expected = {
        "format_version": 1,
        "migration_id": JOB,
        "global_name": "^YHMFTestProduct",
        "source_fingerprint": "d" * 64,
        "target_fingerprint": TARGET,
        "phase": "validate",
        "validated": 0,
        "backfilled": 0,
        "verified": 0,
        "retagged": 0,
        "cursor": "",
    }
    assert store.get(job_path(JOB)) == canonical_json(expected)
    assert load_status(store, JOB) == status


def test_absent_source_is_explicit_and_uses_distinct_checkpoint_version(store):
    status = MigrationStatus(JOB, "^YHMFTestProduct", None, TARGET, operation_fingerprint=OPERATION)
    save_status(store, status)
    document = json.loads(store.get(job_path(JOB)))
    assert document["format_version"] == 2 and document["source_fingerprint"] is None
    assert load_status(store, JOB) == status
    with pytest.raises(MigrationError, match="own maintenance executor"):
        saved_snapshots(store, status)
    with pytest.raises(StorageError, match="explicit maintenance operation"):
        MigrationStatus(JOB, "^YHMFTestProduct", None, TARGET)


@pytest.mark.parametrize("version,source", [(True, None), (3, None), (1, None), (2, "d" * 64)])
def test_mixed_or_unknown_checkpoint_versions_fail_closed(store, version, source):
    status = MigrationStatus(JOB, "^YHMFTestProduct", None, TARGET, operation_fingerprint=OPERATION)
    save_status(store, status)
    document = json.loads(store.get(job_path(JOB)))
    document.update(format_version=version, source_fingerprint=source)
    store.set(job_path(JOB), canonical_json(document))
    with pytest.raises(StorageError, match="Malformed migration checkpoint"):
        load_status(store, JOB)


def test_adoption_barrier_reports_no_installed_schema_and_blocks_managed_access(store):
    class Record(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    db = Database(store, Registry(Record))
    spec = db.registry.spec(Record)
    status = MigrationStatus(
        JOB, spec.global_name, None, spec.fingerprint.decode(), operation_fingerprint=OPERATION
    )
    save_status(store, status)
    store.set(GATE, JOB.encode())
    store.set(schema_path(spec.global_name), marker(JOB))
    store.set(job_path(JOB).child(b"target_snapshot"), spec.snapshot)
    store.set(Path(spec.global_name, (b"s:A", b"id")), b"s:A")
    inspection = db.inspect_schema(Record)
    assert inspection.installed is None and inspection.status == "unmanaged"
    assert inspection.migration_id == JOB
    assert store.get(schema_path(spec.global_name).child(b"snapshot")) is None
    with pytest.raises(MaintenanceActive):
        db.get(Record, "A")
    with pytest.raises(MaintenanceActive):
        db.insert(Record(id="B"))
