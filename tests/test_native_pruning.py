# SPDX-License-Identifier: GPL-3.0-or-later
"""Two pruning processes start from the same checkpoint read inside native TP."""

import json
import select
import subprocess
import sys

import pytest

from mccann_hub.yhmf import NativeStore
from mccann_hub.yhmf.maintenance import job_path
from tests.test_migration_pruning import job

pytestmark = [pytest.mark.native, pytest.mark.adversarial]
WORKER = """
import json, sys
from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.maintenance import job_path

store = NativeStore()
root = job_path(sys.argv[1])
original, paused = store.get, False
def read(path):
    global paused
    value = original(path)
    if path == root and not paused:
        paused = True
        print("ready", flush=True)
        assert sys.stdin.readline().strip() == "go"
    return value
store.get = read
db = Database(store, Registry())
for attempt in range(100):
    if db.prune_migration(sys.argv[1], batch_size=1):
        print(json.dumps({"complete": True}))
        break
else:
    raise AssertionError("Pruning failed to terminate")
"""


def test_concurrent_pruning_is_idempotent_and_leaves_no_artifacts(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native transaction conflict detection")
    _, status = job(store)
    processes = []
    try:
        for _ in range(2):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", WORKER, status.migration_id],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in processes:
            readable, _, _ = select.select([process.stdout], [], [], 20)
            assert readable, "Pruning worker did not reach its read barrier"
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            assert json.loads(stdout)["complete"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
    root = job_path(status.migration_id)
    assert store.get(root) is None and not store.children(root, limit=1)
