# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import json
import os
import runpy
import select
import subprocess
import sys
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import NotFound

pytestmark = pytest.mark.native
_WORKER = FilePath(__file__).with_name("native_references.py")
_MODELS = runpy.run_path(str(_WORKER))
Target, Source = _MODELS["Target"], _MODELS["Source"]


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Native reference races require a disposable YottaDB database")
    store = NativeStore()
    roots = [
        Path("^YHMFTestRefTarget"),
        Path("^YHMFTestRefSource"),
        Path("^YHMFRef", (b"YHMFTestRefTarget",)),
        Path("^YHMFSchema", (b"YHMFTestRefTarget",)),
        Path("^YHMFSchema", (b"YHMFTestRefSource",)),
        Path("^YHMFIndex", (b"YHMFTestRefSource",)),
        Path("^YHMFUnique", (b"YHMFTestRefSource",)),
    ]
    for root in roots:
        store.delete_tree(root)
    yield Database(store, Registry(Target, Source))
    for root in roots:
        store.delete_tree(root)


def _wait_ready(process):
    readable, _, _ = select.select([process.stdout], [], [], 20)
    assert readable, "Race worker did not reach its native read barrier"
    message = process.stdout.readline().strip()
    if message != "ready":
        _, stderr = process.communicate(timeout=5)
        pytest.fail(f"Worker exited before the read barrier: {stderr}")


def _race(*operations):
    processes = []
    try:
        for operation, target_key, source_key in operations:
            processes.append(
                subprocess.Popen(
                    [sys.executable, str(_WORKER), operation, str(target_key), str(source_key)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in processes:
            _wait_ready(process)
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_reference_creation_racing_target_deletion_never_leaves_a_dangling_link(db):
    for key in range(1, 9):
        db.insert(Target(id=key))
        results = _race(("create", key, key), ("delete_target", key, key))
        assert sorted(item["result"] for item in results) == ["committed", "restricted"]
        creator = next(item for item in results if item["operation"] == "create")
        if creator["result"] == "committed":
            assert db.get(Source, key).target.key == db.get(Target, key).id
            assert db.store.get(Path("^YHMFRef", (b"YHMFTestRefTarget", str(key).encode()))) == b"1"
        else:
            with pytest.raises(NotFound):
                db.get(Source, key)
            with pytest.raises(NotFound):
                db.get(Target, key)


def test_concurrent_reference_addition_and_removal_preserve_the_count(db):
    db.insert(Target(id=1))
    db.insert(Source(id=1, target=Reference[Target](key=1)))
    results = _race(("create", 1, 2), ("delete_source", 1, 1))
    assert all(item["result"] == "committed" for item in results)
    assert db.get(Source, 2).target.key == 1
    with pytest.raises(NotFound):
        db.get(Source, 1)
    assert db.store.get(Path("^YHMFRef", (b"YHMFTestRefTarget", b"1"))) == b"1"


def test_reference_index_is_rebuilt_from_retried_state(db):
    yottadb = importlib.import_module("yottadb")
    db.insert(Target(id=1))
    attempts = []
    count_path = Path("^YHMFRef", (b"YHMFTestRefTarget", b"1"))

    def create(tx):
        attempts.append(tx)
        with pytest.raises(NotFound):
            tx.get(Source, 1)
        assert tx.store.get(count_path) is None
        tx.insert(Source(id=1, target=Reference[Target](key=1)))
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(create)
    assert len(attempts) == 2
    assert db.store.get(count_path) == b"1"
    assert db.get(Source, 1).target.key == 1


def test_concurrent_unique_claims_commit_one_complete_record(db):
    db.insert(Target(id=1))
    db.insert(Source(id=0, target=Reference[Target](key=1), token="seed"))
    db.delete(Source, 0)
    results = _race(("claim", 1, 1), ("claim", 1, 2))
    assert sorted(item["result"] for item in results) == ["committed", "restricted"]
    loser = next(item for item in results if item["result"] == "restricted")
    assert loser["error"] == "UniqueViolation"
    for key, result in enumerate(results, start=1):
        if result["result"] == "committed":
            assert db.get(Source, key).token == "shared"
        else:
            with pytest.raises(NotFound):
                db.get(Source, key)
    assert db.store.get(Path("^YHMFRef", (b"YHMFTestRefTarget", b"1"))) == b"1"
    assert db.check_indexes(Source).issues == ()
