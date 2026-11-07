# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated M checks the same durable maintenance ownership as Python."""

import json
import subprocess
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.codecs import canonical_json, encode_key
from mccann_hub.yhmf.maintenance import MigrationStatus, job_path
from mccann_hub.yhmf.schemas import schema_path
from tests import test_m_writers
from tests.test_m_writers import MRecord, values
from tests.test_native_schema_safety import ready

m_writer = test_m_writers.m_writer

pytestmark = pytest.mark.native


class Other(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)
    value: int = Field(default=0, index=True)


class NextOther(Other):
    __schema_version__ = 2
    note: str = "added"


def begin(store, kind):
    manager = Database(store, Registry(Other))
    if kind == "adoption":
        return manager, manager.begin_adoption(Other)
    manager.insert(Other(id="B"))
    if kind == "rebuild":
        return manager, manager.begin_index_rebuild(Other, plan=manager.plan_index_rebuild(Other))
    manager = Database(store, Registry(NextOther))
    plan = manager.plan_migration(NextOther)
    return manager, manager.begin_migration(Other, NextOther, plan=plan)


def replace_source(routine, source):
    routine.write_text(source)
    routine.with_suffix(".o").unlink(missing_ok=True)


def document(**changes):
    status = MigrationStatus("a" * 32, Other.__global__, "b" * 64, "c" * 64)
    result = status.to_dict(include_cursor=True)
    result["format_version"] = 1
    result.update(changes)
    return result


@pytest.mark.parametrize("key", [None, "", "雪😀", "\x00", "\U0010ffff", -999999999999999999])
@pytest.mark.parametrize("version", [1, 2])
def test_m_checkpoint_reader_accepts_existing_formats_unicode_and_exact_counters(
    m_writer, key, version
):
    _, run, _, _ = m_writer
    value = document(validated=10**25, backfilled=10**24)
    if key is not None:
        value["cursor"] = encode_key(key).hex()
    if version == 2:
        value.update(format_version=2, source_fingerprint=None, operation_fingerprint="d" * 64)
    assert run("checkpoint", {"raw": canonical_json(value).decode()}) == "1"


@pytest.mark.parametrize(
    "changes",
    [
        {"format_version": True},
        {"format_version": 3},
        {"source_fingerprint": None},
        {"source_fingerprint": "b" * 63},
        {"operation_fingerprint": "bad"},
        {"phase": "validate|clear_indexes"},
        {"phase": "complete"},
        {"phase": "aborted", "pruning": True},
        {"pruning": "false"},
        {"backfilled": 1},
        {"validated": -1},
        {"verified": True},
        {"cursor": "733ac080"},  # Overlong UTF-8 encoding.
        {"cursor": "733aeda080"},  # Surrogate code point.
        {"cursor": "733af4908080"},  # Beyond U+10FFFF.
        {"cursor": "733ae282"},  # Truncated sequence.
        {"cursor": "3031"},  # Numeric alias 01.
        {"cursor": "zz"},
        {"global_name": "^%YReserved"},
        {"global_name": "^bad,name"},
        {"global_name|migration_id": "unexpected"},
        {"unexpected": 0},
    ],
)
def test_m_checkpoint_reader_refuses_unknown_or_corrupt_scope(m_writer, changes):
    _, run, _, _ = m_writer
    assert run("checkpoint", {"raw": canonical_json(document(**changes)).decode()}) == "0"


def test_m_checkpoint_reader_requires_canonical_encoding(m_writer):
    _, run, _, _ = m_writer
    assert run("checkpoint", {"raw": json.dumps(document())}) == "0"


@pytest.mark.parametrize("kind", ["migration", "rebuild", "adoption"])
def test_unrelated_m_writes_continue_during_each_maintenance_operation(store, m_writer, kind):
    db, run, _, _ = m_writer
    assert run("insert", values()) == "ok"
    manager, job = begin(store, kind)
    assert run("replace", values(sku="T")) == "ok"
    assert db.get(MRecord, "A").sku == "T"
    assert db.check_indexes(MRecord).issues == ()
    manager = Database(
        store, Registry(NextOther if kind == "migration" else Other)
    )  # Reopen the checkpoint.
    manager.abort_migration(job.migration_id)
    assert run("replace", values(sku="U")) == "ok"


@pytest.mark.parametrize(
    "damage", ["missing", "json", "unknown_version", "identity", "barrier", "terminal"]
)
def test_untrusted_job_blocks_even_unrelated_m_writes(store, m_writer, damage):
    _, run, _, _ = m_writer
    assert run("insert", values()) == "ok"
    _, job = begin(store, "migration")
    path = job_path(job.migration_id)
    if damage == "missing":
        store.delete_tree(path)
    elif damage == "json":
        store.set(path, b"not-json")
    elif damage == "barrier":
        store.set(schema_path(Other.__global__), b"bad")
    else:
        data = json.loads(store.get(path))
        data.update(
            {
                "unknown_version": {"format_version": 3},
                "identity": {"migration_id": "e" * 32},
                "terminal": {"phase": "complete"},
            }[damage]
        )
        store.set(path, canonical_json(data))
    assert run("replace", values(sku="T")) == "maintenance"
    assert store.get(Path(MRecord.__global__, (b"s:A", b"sku"))) == b"s:S"


def test_affected_m_writer_stays_blocked_and_old_source_keeps_conservative_gate(store, m_writer):
    db, run, routine, _ = m_writer
    assert run("insert", values()) == "ok"
    _, other_job = begin(store, "migration")
    replace_source(
        routine,
        routine.read_text().replace(
            ' if \'$$available(cfg("global"))', ' if $data(^YHMFMigration("gate"))'
        ),
    )
    assert run("replace", values(sku="T")) == "maintenance"
    db.abort_migration(other_job.migration_id)
    replace_source(routine, db.m_routine(MRecord, name="YHMFTAPI"))

    class NextRecord(MRecord):
        __schema_version__ = 2
        note: str = "new"

    manager = Database(store, Registry(NextRecord))
    job = manager.begin_migration(MRecord, NextRecord, plan=manager.plan_migration(NextRecord))
    assert run("replace", values(sku="T")) == "maintenance"
    manager.abort_migration(job.migration_id)
    assert run("replace", values(sku="T")) == "ok"


def test_native_m_writer_rechecks_scope_when_migration_advances_during_its_attempt(store, m_writer):
    db, run, routine, environment = m_writer
    assert run("insert", values()) == "ok"
    manager, job = begin(store, "migration")
    original = routine.read_text()
    replace_source(
        routine,
        routine.read_text().replace(
            " ; YHMF writer checkpoint:",
            ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
            ' . if reply\'="go" set $ecode=",U1004,"\n ; YHMF writer checkpoint:',
        ),
    )
    command = str(FilePath(environment["ydb_dist"]) / "yottadb")
    worker = subprocess.Popen(
        [command, "-run", "%XCMD", 'do worker^YHMFWTEST("C","C-SKU")'],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        advanced = manager.advance_migration(job.migration_id, Other, NextOther, batch_size=1)
        assert advanced != job
        output, error = worker.communicate("go\n", timeout=20)
        assert worker.returncode == 0 and not error, error
        assert output.strip() == "ok"
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
    replace_source(routine, original)
    assert db.get(MRecord, "C").sku == "C-SKU"
    for _ in range(25):
        advanced = manager.advance_migration(job.migration_id, Other, NextOther, batch_size=1)
        assert run("replace", values(sku="T")) == "ok"
        if advanced.phase == "complete":
            break
    assert advanced.phase == "complete"
    assert db.check_indexes(MRecord).issues == ()
