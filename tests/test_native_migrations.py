# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import json
import os
import runpy
import select
import subprocess
import sys
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from mccann_hub.yhmf.maintenance import job_path

pytestmark = pytest.mark.native
_WORKER = FilePath(__file__).with_name("native_migrations.py")
_models = runpy.run_path(str(_WORKER))
Product, ProductV2 = _models["Product"], _models["ProductV2"]


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Migration process tests require a disposable native database")
    store = NativeStore()
    roots = [
        Path(name)
        for name in (
            "^YHMFTestProduct",
            "^YHMFSchema",
            "^YHMFIndex",
            "^YHMFUnique",
            "^YHMFMigration",
        )
    ]
    for root in roots:
        store.delete_tree(root)
    old = Database(store, Registry(Product))
    for number in range(3):
        old.insert(Product(id=f"P{number}", name="Main", sku=f"SKU{number}", price=Decimal("1.00")))
    yield Database(store, Registry(ProductV2))
    for root in roots:
        store.delete_tree(root)


def start(db):
    return db.begin_migration(Product, ProductV2, plan=db.plan_migration(ProductV2))


def advance_to(db, status, phase):
    for _ in range(100):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    raise AssertionError("Migration did not reach its target phase")


def worker(operation, migration_id="0" * 32):
    return subprocess.Popen(
        [sys.executable, str(_WORKER), operation, migration_id],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def ready(process):
    readable, _, _ = select.select([process.stdout], [], [], 20)
    assert readable, "Migration worker did not reach the native transaction barrier"
    message = process.stdout.readline().strip()
    if message != "ready":
        _, stderr = process.communicate(timeout=5)
        pytest.fail(f"Worker exited before its barrier: {message} {stderr}")


def result(process):
    stdout, stderr = process.communicate(input="go\n", timeout=20)
    assert process.returncode == 0, stderr
    return json.loads(stdout)


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


@pytest.mark.parametrize("phase", ["backfill", "activate"])
def test_native_restart_reloads_checkpoint_and_returns_committed_attempt(db, monkeypatch, phase):
    yottadb = importlib.import_module("yottadb")
    status = advance_to(db, start(db), phase)
    original = db.store.set
    attempts = []

    def restart_after_checkpoint(path, value):
        original(path, value)
        if path == job_path(status.migration_id):
            attempts.append(json.loads(value))
            if len(attempts) == 1:
                raise yottadb.YDBTPRestart()

    monkeypatch.setattr(db.store, "set", restart_after_checkpoint)
    updated = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert db.migration_status(status.migration_id) == updated
    monkeypatch.setattr(db.store, "set", original)
    completed = advance_to(db, updated, "complete")
    assert completed.retagged == 3 and db.check_indexes(ProductV2).issues == ()


def test_competing_native_workers_resume_from_the_latest_committed_cursor(db):
    status = start(db)
    processes = [worker("advance", status.migration_id) for _ in range(2)]
    try:
        for process in processes:
            ready(process)
        # Release both before waiting for either, so their original snapshots conflict.
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        summaries = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            summaries.append(json.loads(stdout))
    finally:
        for process in processes:
            stop(process)
    assert sorted(item["validated"] for item in summaries) == [1, 2]
    assert sum(item["reads"] for item in summaries) >= 3
    resumed = db.migration_status(status.migration_id)
    assert resumed.validated == 2
    assert advance_to(db, resumed, "complete").retagged == 3


def test_competing_native_starts_claim_only_one_job(db):
    processes = [worker("begin") for _ in range(2)]
    try:
        for process in processes:
            ready(process)
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        summaries = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            summaries.append(json.loads(stdout))
    finally:
        for process in processes:
            stop(process)
    winner = next(item for item in summaries if "migration_id" in item)
    assert sum(item.get("result") == "restricted" for item in summaries) == 1
    assert db.store.children(Path("^YHMFMigration", (b"jobs",)), limit=10) == (
        winner["migration_id"].encode(),
    )
    db.abort_migration(winner["migration_id"])


def test_process_death_during_backfill_leaves_the_previous_checkpoint_resumable(db):
    status = advance_to(db, start(db), "backfill")
    process = worker("crash", status.migration_id)
    try:
        ready(process)
        process.kill()
        process.wait(timeout=5)
    finally:
        stop(process)
    assert db.migration_status(status.migration_id) == status
    assert db.store.get(Path(Product.__global__, (b"s:P0", b"note"))) is None
    # A fresh process resumes the same job, using persisted snapshots and cursor.
    process = worker("resume", status.migration_id)
    try:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["backfilled"] == 1
    finally:
        stop(process)
    assert advance_to(db, db.migration_status(status.migration_id), "complete").retagged == 3


@pytest.mark.parametrize("operation", ["write", "legacy_write"])
def test_inflight_native_writers_retry_into_the_maintenance_barrier(db, operation):
    process = worker(operation)
    try:
        ready(process)
        status = start(db)
        summary = result(process)
    finally:
        stop(process)
    assert summary["result"] == "restricted" and summary["reads"] >= 2
    db.abort_migration(status.migration_id)
    old = Database(db.store, Registry(Product))
    assert old.get(Product, "P0").name == "Main"
