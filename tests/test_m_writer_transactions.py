# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.schemas import schema_path
from tests import test_m_writers
from tests.test_m_writer_maintenance import replace_source
from tests.test_m_writers import MRecord, values
from tests.test_native_schema_safety import ready

m_writer = test_m_writers.m_writer
pytestmark = pytest.mark.native


class Other(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)
    value: int = Field(unique=True, ge=0, le=100)


@pytest.fixture
def participants(store, m_writer):
    db, _, routine, environment = m_writer
    driver = FilePath(__file__).with_name("m") / "YHMFPART.m"
    (routine.parent / driver.name).write_bytes(driver.read_bytes())
    other = Database(store, Registry(Other))
    other_source = routine.with_name("YHMFOAPI.m")
    other_source.write_text(other.m_routine(Other, name="YHMFOAPI"))
    root = Path("^YHMFTestStorage", (b"writerInput",))

    def run(operation, data):
        store.delete_tree(root)
        for field, value in data.items():
            store.set(root.child(field.encode("ascii")), value.encode("utf-8"))
        command = str(FilePath(environment["ydb_dist"]) / "yottadb")
        result = subprocess.run(
            [command, "-run", f"{operation}^YHMFPART"],
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        assert not result.stderr, result.stderr
        return result.stdout.strip()

    return db, other, run, routine, other_source, environment


def test_participants_require_an_existing_transaction_without_installing_schema(
    store, participants
):
    _, _, run, _, _, _ = participants
    assert run("outside", values()) == "transaction|0"
    assert store.get(schema_path(MRecord.__global__)) is None
    assert store.get(schema_path(Other.__global__)) is None


def test_two_models_commit_once_and_participant_replacement_and_deletion_work(participants):
    db, other, run, _, _, _ = participants
    assert run("pair", values()) == "pending|1\nok|0"
    assert db.get(MRecord, "A").sku == "S"
    assert other.get(Other, "B").value == 1
    assert run("replaceanddelete", values(sku="T")) == "ok|0"
    assert db.get(MRecord, "A").sku == "T"
    assert other.scan(Other) == []
    assert db.check_indexes(MRecord).issues == other.check_indexes(Other).issues == ()


@pytest.mark.parametrize(
    "failure", ["first_input", "second_input", "second_unique", "second_missing"]
)
def test_participant_failure_aborts_all_prior_writes_and_schema_installation(
    store, participants, failure
):
    db, other, run, _, _, _ = participants
    data = values()
    operation, expected = "pair", "invalid"
    if failure == "first_input":
        data["quantity"] = "-1"
    elif failure == "second_input":
        data["second"] = "-1"
    elif failure == "second_unique":
        other.insert(Other(id="EXISTING", value=1))
        expected = "unique"
    else:
        other.insert(Other(id="C", value=2))
        other.delete(Other, "C")
        db.insert(MRecord(id="A", sku="S", name="Stock", quantity=1))
        data["sku"] = "T"
        operation, expected = "replaceanddelete", "missing"
    assert run(operation, data) == expected + "|0"
    assert db.scan(MRecord) == (
        [MRecord(id="A", sku="S", name="Stock", quantity=1)] if failure == "second_missing" else []
    )
    assert other.scan(Other) == (
        [Other(id="EXISTING", value=1)] if failure == "second_unique" else []
    )
    if failure != "second_missing":
        assert store.get(schema_path(MRecord.__global__)) is None
    assert db.check_indexes(MRecord).issues == other.check_indexes(Other).issues == ()


def test_unexpected_second_participant_error_rolls_back_first_model(store, participants):
    db, _, run, _, other_source, _ = participants
    replace_source(
        other_source,
        other_source.read_text().replace(
            " if install set ^YHMFSchema", ' set $ecode=",U1005,"\n if install set ^YHMFSchema'
        ),
    )
    assert run("caught", values()) == "error|0"
    assert db.scan(MRecord) == []
    assert store.get(schema_path(MRecord.__global__)) is None
    assert store.get(schema_path(Other.__global__)) is None
    assert store.children(Path("^YHMFIndex"), limit=1) == ()
    assert store.children(Path("^YHMFUnique"), limit=1) == ()


def test_forced_restart_repeats_entire_caller_transaction(participants):
    db, other, run, _, other_source, _ = participants
    replace_source(
        other_source,
        other_source.read_text().replace(
            " if install set ^YHMFSchema", " if $trestart=0 trestart\n if install set ^YHMFSchema"
        ),
    )
    assert run("pair", values()) == "pending|1\nok|0"
    assert [row.id for row in db.scan(MRecord)] == ["A"]
    assert [row.id for row in other.scan(Other)] == ["B"]
    assert db.check_indexes(MRecord).issues == other.check_indexes(Other).issues == ()


def test_native_restart_rereads_business_values_after_concurrent_python_write(store, participants):
    db, other, run, routine, _, environment = participants
    db.insert(MRecord(id="A", sku="S", name="Stock", quantity=5))
    # Prepare the complete input outside the worker's transaction, but compute
    # quantity inside the outer restart point in increment^YHMFPART.
    root = Path("^YHMFTestStorage", (b"writerInput",))
    for field, value in values().items():
        store.set(root.child(field.encode()), value.encode())
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
        [command, "-run", "increment^YHMFPART"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        db.patch(MRecord, "A", changes={"quantity": 10})
        output, error = worker.communicate("go\n", timeout=20)
        assert worker.returncode == 0 and not error, error
        assert output.strip() == "ok|0"
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
    assert db.get(MRecord, "A").quantity == 11
    assert other.get(Other, "B").value == 1
    assert db.check_indexes(MRecord).issues == other.check_indexes(Other).issues == ()


def test_second_participant_maintenance_failure_aborts_prior_model(store, participants):
    db, other, run, _, _, _ = participants
    job = other.begin_adoption(Other)
    assert run("pair", values()) == "maintenance|0"
    assert db.scan(MRecord) == []
    assert store.get(schema_path(MRecord.__global__)) is None
    assert other.migration_status(job.migration_id) == job
    other.abort_migration(job.migration_id)
    assert other.scan(Other) == []


@pytest.mark.parametrize("fail", [False, True])
def test_participants_leave_successful_nesting_to_caller_and_abort_every_level_on_failure(
    store, participants, fail
):
    _, _, run, _, _, _ = participants
    data = values()
    if fail:
        data["second"] = "-1"
    assert run("nestedpair", data) == ("invalid|0\nouter|0" if fail else "pending|2\nok|1\nouter|0")
    if fail:
        assert store.get(schema_path(MRecord.__global__)) is None
        assert store.get(schema_path(Other.__global__)) is None
