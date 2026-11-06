# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from tests.native_schema_safety import Counter
from tests.test_native_schema_safety import ready

pytestmark = [pytest.mark.native, pytest.mark.adversarial]


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Requires disposable YottaDB")
    store = NativeStore()
    roots = [Path(Counter.__global__), Path("^YHMFSchema", (b"YHMFTestProduct",))]
    for root in roots:
        store.delete_tree(root)
    db = Database(store, Registry(Counter))
    db.insert(Counter(id=1, count=10))
    db.insert(Counter(id=2))
    yield db
    for root in roots:
        store.delete_tree(root)


@pytest.mark.parametrize("signal", ["YDBTPRestart", "YDBTPRollback"])
def test_caught_read_signal_restarts_or_aborts_the_whole_native_attempt(db, monkeypatch, signal):
    yottadb = importlib.import_module("yottadb")
    original, injected, observed = yottadb.get, [], []

    def get(name, subscripts=()):
        if not injected and name == Counter.__global__ and subscripts == (b"1", b"count"):
            injected.append(True)
            raise getattr(yottadb, signal)()
        return original(name, subscripts)

    def update(tx):
        pending = tx.get(Counter, 2)
        observed.append(pending.count)
        tx.patch(Counter, 2, changes={"count": pending.count + 1})
        try:
            return tx.get(Counter, 1).count
        except Exception:
            return 0

    with monkeypatch.context() as patch:
        patch.setattr(yottadb, "get", get)
        if signal == "YDBTPRestart":
            assert db.atomic(update) == 10
        else:
            with pytest.raises(yottadb.YDBTPRollback):
                db.atomic(update)
    assert injected == [True]
    assert observed == ([0, 0] if signal == "YDBTPRestart" else [0])
    assert db.get(Counter, 2).count == (1 if signal == "YDBTPRestart" else 0)


@pytest.mark.parametrize("broad", [False, True])
def test_four_process_counter_has_no_lost_updates_with_broad_read_handlers(db, broad):
    db.patch(Counter, 1, changes={"count": 0})
    workers, results = [], []
    iterations = 1500
    try:
        for _ in range(4):
            workers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "tests.native_transaction_control",
                        str(iterations),
                        "broad" if broad else "plain",
                    ],
                    cwd=FilePath(__file__).parents[1],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for worker in workers:
            ready(worker)
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        for worker in workers:
            output, error = worker.communicate(timeout=90)
            assert worker.returncode == 0, error
            result = json.loads(output)
            results.append(result)
            assert result["commits"] == iterations
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    assert db.get(Counter, 1).count == 4 * iterations, results
