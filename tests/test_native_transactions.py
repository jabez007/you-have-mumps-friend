# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from mccann_hub.yhmf.errors import NotFound, TransactionError

pytestmark = pytest.mark.native
_WORKER = FilePath(__file__).with_name("native_counter.py")
Counter = runpy.run_path(str(_WORKER))["Counter"]


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Native restart and process tests require a disposable YottaDB database")
    store = NativeStore()
    root = Path("^YHMFTestCounter")
    schema = Path("^YHMFSchema", (b"YHMFTestCounter",))
    store.delete_tree(root)
    store.delete_tree(schema)
    yield Database(store, Registry(Counter))
    store.delete_tree(root)
    store.delete_tree(schema)


def test_engine_restart_reloads_state_and_expires_both_handles(db):
    yottadb = importlib.import_module("yottadb")
    db.insert(Counter(id=1))
    handles = []
    observed = []

    def increment(tx):
        handles.append(tx)
        counter = tx.get(Counter, 1)
        observed.append(counter.count)
        counter.count += 1
        counter.mirror = counter.count
        tx.save(counter)
        if len(handles) == 1:
            raise yottadb.YDBTPRestart()
        return counter.count

    assert db.atomic(increment) == 1
    assert observed == [0, 0]
    assert handles[0] is not handles[1]
    assert db.get(Counter, 1).count == 1
    for tx in handles:
        with pytest.raises(TransactionError):
            tx.get(Counter, 1)


def test_abort_rolls_back_schema_installation_and_all_record_nodes(db):
    def abort(tx):
        tx.insert(Counter(id=1))
        raise RuntimeError("abort initial installation")

    with pytest.raises(RuntimeError, match="abort initial"):
        db.atomic(abort)
    assert db.store.get(Path("^YHMFSchema", (b"YHMFTestCounter",))) is None
    assert db.store.children(Path("^YHMFTestCounter"), limit=1) == ()
    with pytest.raises(NotFound):
        db.get(Counter, 1)


def test_concurrent_processes_do_not_lose_updates_or_expose_torn_reads(db):
    db.insert(Counter(id=1))
    processes = []
    summaries = []
    try:
        for _ in range(4):
            processes.append(
                subprocess.Popen(
                    [sys.executable, str(_WORKER), "40"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        deadline = time.monotonic() + 45
        while any(process.poll() is None for process in processes):
            counter = db.get(Counter, 1)
            assert counter.count == counter.mirror
            assert time.monotonic() < deadline, "Native workers did not finish within 45 seconds"
            time.sleep(0.001)
        for process in processes:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
            summaries.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
    assert db.get(Counter, 1).count == 160
    assert sum(summary["commits"] for summary in summaries) == 160
    assert sum(summary["attempts"] for summary in summaries) >= 160


def test_persistent_native_validation_failure_reaches_stable_attempt_and_rolls_back(db):
    yottadb = importlib.import_module("yottadb")
    from mccann_hub.yhmf.errors import StorageError

    attempts = []

    def fail(tx):
        tx.insert(Counter(id=1))
        attempts.append(int(yottadb.get("$trestart")))
        raise StorageError("persistent validation failure")

    with pytest.raises(StorageError, match="persistent validation"):
        db.atomic(fail)
    assert attempts == [0, 1, 2, 3]
    assert db.store.get(Path("^YHMFSchema", (b"YHMFTestCounter",))) is None
    assert db.store.children(Path("^YHMFTestCounter"), limit=1) == ()
