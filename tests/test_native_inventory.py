# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, Inventory, Movement, Product
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from tests.test_inventory_example import DOCK, ROOTS, STORAGE, TIME
from tests.test_native_schema_safety import ready

pytestmark = pytest.mark.native


@pytest.fixture
def inventory():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Requires disposable YottaDB")
    store = NativeStore()
    for name in ROOTS:
        store.delete_tree(Path(name))
    db = Database(store, Registry(*MODELS))
    seed(db)
    yield Inventory(db)
    for name in ROOTS:
        store.delete_tree(Path(name))


def test_competing_moves_cannot_double_spend_stock(inventory):
    inventory.receive("00", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    workers = []
    try:
        for movement in ("01", "02"):
            workers.append(
                subprocess.Popen(
                    [sys.executable, "-m", "tests.native_inventory", movement],
                    cwd=FilePath(__file__).parents[1],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for worker in workers:
            ready(worker)
        results = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0, error
            results.append(output.strip())
        assert results == ["committed", "InsufficientStock"]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    assert inventory.stock_at(DOCK)[0].quantity == 5
    assert inventory.stock_at(STORAGE)[0].quantity == 15
    assert [movement.id for movement in inventory.history("LOT-001")] == ["00", "01"]
    for model in MODELS:
        assert inventory.db.check_indexes(model).issues == ()


def test_native_restart_repeats_the_workflow_with_one_stable_ledger_identity(
    inventory, monkeypatch
):
    yottadb = importlib.import_module("yottadb")
    store, observed = inventory.db.store, []
    original = store.set

    def restart_once(path, value):
        original(path, value)
        if path.global_name == Movement.__global__ and path.subscripts[-1:] == (b"occurred_at",):
            observed.append(value)
            if len(observed) == 1:
                raise yottadb.YDBTPRestart()

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", restart_once)
        inventory.receive("01", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    assert observed == [b"T:2026-11-06T12:00:00.000000Z"] * 2
    assert inventory.stock_at(DOCK)[0].quantity == 20
    assert len(inventory.history("LOT-001")) == 1
    assert store.get(Path("^YHMFRef", (Product.__global__[1:].encode(), b"s:WIDGET"))) == b"3"
    for model in MODELS:
        assert inventory.db.check_indexes(model).issues == ()
