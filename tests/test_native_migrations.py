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
_unique_models = _models["_unique_models"]
UniqueSource, UniqueTarget = _unique_models["Product"], _unique_models["UniqueProduct"]
_nested_models = _models["_nested_models"]
Warehouse, WarehouseV2 = _nested_models["Warehouse"], _nested_models["WarehouseV2"]
RelatedTarget = _nested_models["Target"]


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


def start(db, source=Product, target=ProductV2):
    return db.begin_migration(source, target, plan=db.plan_migration(target))


def advance_to(db, status, phase, source=Product, target=ProductV2):
    for _ in range(100):
        if status.phase == phase:
            return status
        status = db.advance_migration(status.migration_id, source, target, batch_size=1)
    raise AssertionError("Migration did not reach its target phase")


def worker(operation, migration_id="0" * 32, kind="additive"):
    return subprocess.Popen(
        [sys.executable, str(_WORKER), operation, migration_id, kind],
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
def test_inflight_native_writers_cannot_cross_the_maintenance_barrier(db, operation):
    process = worker(operation)
    try:
        ready(process)
        status = start(db)
        summary = result(process)
    finally:
        stop(process)
    assert summary["result"] == "restricted" and summary["reads"] >= 1
    if operation == "legacy_write":
        # A legacy writer read the old fingerprint before acquisition, so its
        # eventual rejection also proves the transaction retried that read.
        assert summary["reads"] >= 2
    db.abort_migration(status.migration_id)
    old = Database(db.store, Registry(Product))
    assert old.get(Product, "P0").name == "Main"


@pytest.fixture
def unique_db(db):
    # The existing fixture owns these disposable globals and their final cleanup.
    for name in (Product.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFMigration"):
        db.store.delete_tree(Path(name))
    old = Database(db.store, Registry(UniqueSource))
    for number in range(3):
        old.insert(
            UniqueSource(
                id=f"P{number}", sku=f"SKU{number}", serial=f"S{number}", price=Decimal(number + 1)
            )
        )
    return Database(db.store, Registry(UniqueTarget))


def unique_start(db):
    return start(db, UniqueSource, UniqueTarget)


def unique_advance_to(db, status, phase):
    return advance_to(db, status, phase, UniqueSource, UniqueTarget)


@pytest.mark.parametrize("phase", ["validate", "backfill", "activate"])
def test_unique_native_restart_commits_only_one_reservation_and_claim_set(
    unique_db, monkeypatch, phase
):
    db = unique_db
    status = unique_advance_to(db, unique_start(db), phase)
    yottadb = importlib.import_module("yottadb")
    original = db.store.set
    checkpoints = []

    def restart_once(path, value):
        original(path, value)
        if path == job_path(status.migration_id):
            checkpoints.append(value)
            if len(checkpoints) == 1:
                raise yottadb.YDBTPRestart()

    monkeypatch.setattr(db.store, "set", restart_once)
    updated = db.advance_migration(status.migration_id, UniqueSource, UniqueTarget, batch_size=1)
    assert len(checkpoints) == 2 and checkpoints[0] == checkpoints[1]
    assert db.migration_status(status.migration_id) == updated
    monkeypatch.setattr(db.store, "set", original)
    assert unique_advance_to(db, updated, "complete").retagged == 3
    assert db.check_indexes(UniqueTarget).issues == ()


def migration_worker_race(migration_id, kind="unique"):
    processes = [worker("advance", migration_id, kind) for _ in range(2)]
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
        return summaries
    finally:
        for process in processes:
            stop(process)


@pytest.mark.parametrize("duplicate", [False, True])
def test_unique_preflight_workers_serialize_reservations_and_find_cross_batch_duplicates(
    unique_db, duplicate
):
    db = unique_db
    if duplicate:
        old = Database(db.store, Registry(UniqueSource))
        record = old.get(UniqueSource, "P1")
        record.sku = "SKU0"
        old.save(record)
    status = unique_start(db)
    summaries = migration_worker_race(status.migration_id)
    current = db.migration_status(status.migration_id)
    if duplicate:
        assert sum(item.get("error") == "UniqueViolation" for item in summaries) == 1
        assert current.validated == 1 and current.backfilled == 0
        assert len(db.store.children(job_path(status.migration_id).child(b"unique"), limit=10)) == 2
        db.abort_migration(status.migration_id)
        assert old.get(UniqueSource, "P1").sku == "SKU0"
    else:
        assert sorted(item["validated"] for item in summaries) == [1, 2]
        assert sum(item["reads"] for item in summaries) >= 3
        assert current.validated == 2
        assert unique_advance_to(db, current, "complete").retagged == 3


@pytest.mark.parametrize(
    "operation,phase", [("crash_reservation", "validate"), ("crash_claim", "backfill")]
)
def test_unique_worker_death_rolls_back_reservations_or_claims_before_resume(
    unique_db, operation, phase
):
    db = unique_db
    status = unique_advance_to(db, unique_start(db), phase)
    process = worker(operation, status.migration_id, "unique")
    try:
        ready(process)
        process.kill()
        process.wait(timeout=5)
    finally:
        stop(process)
    assert db.migration_status(status.migration_id) == status
    if phase == "validate":
        assert db.store.children(job_path(status.migration_id).child(b"unique"), limit=10) == ()
    for field in (b"sku", b"price"):
        assert db.store.children(Path("^YHMFUnique", (b"YHMFTestProduct", field)), limit=10) == ()
    process = worker("resume", status.migration_id, "unique")
    try:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["phase"] == phase
    finally:
        stop(process)
    current = db.migration_status(status.migration_id)
    assert unique_advance_to(db, current, "complete").retagged == 3
    assert db.check_indexes(UniqueTarget).issues == ()


@pytest.fixture
def nested_db(db):
    names = (
        Product.__global__,
        Warehouse.__global__,
        RelatedTarget.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
        "^YHMFMigration",
    )
    for name in names:
        db.store.delete_tree(Path(name))
    old = Database(db.store, Registry(RelatedTarget, Warehouse))
    old.insert(RelatedTarget(id="T1"))
    for record in _nested_models["sample_records"]():
        old.insert(record)
    yield Database(db.store, Registry(RelatedTarget, WarehouseV2))
    for name in names:
        db.store.delete_tree(Path(name))


def nested_start(db):
    return start(db, Warehouse, WarehouseV2)


def nested_advance_to(db, status, phase):
    return advance_to(db, status, phase, Warehouse, WarehouseV2)


@pytest.mark.parametrize("phase", ["validate", "backfill", "activate"])
def test_native_nested_restart_reloads_whole_parent_and_its_checkpoint(
    nested_db, monkeypatch, phase
):
    db = nested_db
    status = nested_advance_to(db, nested_start(db), phase)
    yottadb = importlib.import_module("yottadb")
    original = db.store.set
    checkpoints = []

    def restart_once(path, value):
        original(path, value)
        if path == job_path(status.migration_id):
            checkpoints.append(value)
            if len(checkpoints) == 1:
                raise yottadb.YDBTPRestart()

    monkeypatch.setattr(db.store, "set", restart_once)
    updated = db.advance_migration(status.migration_id, Warehouse, WarehouseV2, batch_size=1)
    assert len(checkpoints) == 2 and checkpoints[0] == checkpoints[1]
    assert db.migration_status(status.migration_id) == updated
    monkeypatch.setattr(db.store, "set", original)
    assert nested_advance_to(db, updated, "complete").retagged == 3
    assert db.get(WarehouseV2, "W1").lots[-1].samples["μ"].quality == 7
    assert db.check_indexes(WarehouseV2).issues == ()


@pytest.mark.parametrize("duplicate", [False, True])
def test_native_nested_preflight_workers_keep_parent_cursors_and_unique_reservations(
    nested_db, duplicate
):
    db = nested_db
    if duplicate:
        old = Database(db.store, Registry(RelatedTarget, Warehouse))
        record = old.get(Warehouse, "W2")
        record.lots[0].serial = "private-A"
        old.save(record)
    status = nested_start(db)
    summaries = migration_worker_race(status.migration_id, kind="nested")
    current = db.migration_status(status.migration_id)
    if duplicate:
        assert sum(item.get("error") == "UniqueViolation" for item in summaries) == 1
        assert current.validated == 1 and current.backfilled == 0
        assert len(db.store.children(job_path(status.migration_id).child(b"unique"), limit=10)) == 3
        db.abort_migration(status.migration_id)
        assert old.get(Warehouse, "W2").lots[0].serial == "private-A"
    else:
        assert sorted(item["validated"] for item in summaries) == [1, 2]
        assert sum(item["reads"] for item in summaries) >= 3
        assert nested_advance_to(db, current, "complete").retagged == 3


@pytest.mark.parametrize(
    "operation,phase",
    [
        ("crash_reservation", "validate"),
        ("crash_nested_field", "backfill"),
        ("crash_claim", "backfill"),
    ],
)
def test_native_worker_death_cannot_expose_a_partial_parent_tree(nested_db, operation, phase):
    db = nested_db
    status = nested_advance_to(db, nested_start(db), phase)
    process = worker(operation, status.migration_id, kind="nested")
    try:
        ready(process)
        process.kill()
        process.wait(timeout=5)
    finally:
        stop(process)
    assert db.migration_status(status.migration_id) == status
    root = Path(Warehouse.__global__, (b"s:W1",))
    assert db.store.get(root.child(b"note")) is None
    assert db.store.get(root.child(b"header", b"note")) is None
    assert db.store.get(root.child(b"lots", b"-1", b"note")) is None
    if phase == "validate":
        assert db.store.children(job_path(status.migration_id).child(b"unique"), limit=10) == ()
    for field in (b"lots.serial", b"lots.samples.reading"):
        assert db.store.children(Path("^YHMFUnique", (b"YHMFTestWarehouse", field)), limit=10) == ()
    process = worker("resume", status.migration_id, kind="nested")
    try:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["phase"] == phase
    finally:
        stop(process)
    current = db.migration_status(status.migration_id)
    assert nested_advance_to(db, current, "complete").retagged == 3
    assert db.get(WarehouseV2, "W2").header is None
    assert db.check_indexes(WarehouseV2).issues == ()
