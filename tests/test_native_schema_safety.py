# SPDX-License-Identifier: GPL-3.0-or-later
import os
import runpy
import selectors
import subprocess
import sys
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry

pytestmark = pytest.mark.native
WORKER = FilePath(__file__).with_name("native_schema_safety.py")
Counter = runpy.run_path(str(WORKER))["Counter"]


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Requires a disposable YottaDB database")
    store = NativeStore()
    roots = [Path("^YHMFTestProduct"), Path("^YHMFSchema", (b"YHMFTestProduct",))]
    for root in roots:
        store.delete_tree(root)
    yield Database(store, Registry(Counter))
    for root in roots:
        store.delete_tree(root)


def ready(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(15), "Worker did not reach rendezvous"
    assert process.stdout.readline().strip() == "ready"


@pytest.mark.parametrize("operation, expected", [("install", None), ("detached", 1), ("atomic", 2)])
def test_two_process_installation_and_write_semantics(db, operation, expected):
    if operation != "install":
        db.insert(Counter(id=1))
    workers = []
    try:
        for key in (1, 2):
            process = subprocess.Popen(
                [sys.executable, str(WORKER), operation, str(key)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(process)
        for process in workers:
            ready(process)
        # Both read the old state. Commit the first before releasing the second.
        # Native TP must restart stale transactions; detached save remains stale.
        for process in workers:
            output, error = process.communicate("go\n", timeout=20)
            assert process.returncode == 0, error
            assert output.strip() == "committed"
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
    if operation == "install":
        assert db.get(Counter, 1).count == db.get(Counter, 2).count == 0
        assert db.inspect_schema(Counter).status == "matching"
    else:
        assert db.get(Counter, 1).count == expected
