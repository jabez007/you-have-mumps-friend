# SPDX-License-Identifier: GPL-3.0-or-later
"""A bounded copy/clear/checkpoint transaction, including actual native rollback."""

from decimal import Decimal

import pytest

from examples.inventory import BinAddress, Inventory, Warehouse
from examples.inventory.__main__ import seed
from examples.inventory.bin_migration import advance_bin_move, begin_bin_move
from examples.inventory.bin_models import BIN_MODELS, StockBin, StockLocation
from examples.inventory.bins import BinInventory, create_bin
from examples.inventory.ledger import InventoryError
from mccann_hub.yhmf import Database, Registry
from tests.adversarial_support import dump
from tests.test_inventory_bins import ROOTS


def setup(store):
    db = Database(store, Registry(*BIN_MODELS))
    seed(db)
    Inventory(db).receive(
        "OLD",
        "LOT-001",
        BinAddress("MAIN", "DOCK", "B1"),
        Decimal(5),
        occurred_at="2026-11-07T10:00:00Z",
    )
    return db


def finish(store):
    state = None
    for _ in range(5):
        db = Database(store, Registry(*BIN_MODELS))
        state = advance_bin_move(db, writers_stopped=True)
        if state.status == "complete":
            return db
    raise AssertionError(f"Move did not finish: {state}")


def test_explicit_move_preserves_ledger_and_coordinate_stock_and_resumes(store):
    db = setup(store)
    before_movement = dump(store, ("^InventoryMovement",))
    with pytest.raises(InventoryError, match="Stop all"):
        begin_bin_move(db, writers_stopped=False)
    assert begin_bin_move(db, writers_stopped=True).status == "copying"
    with pytest.raises(InventoryError, match="in progress"):
        BinInventory(db).receive("BLOCKED", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal(1))
    first = advance_bin_move(db, writers_stopped=True)
    assert first.after == "MAIN" and first.status == "copying"
    db = finish(store)
    assert advance_bin_move(db, writers_stopped=True).status == "complete"
    assert begin_bin_move(db, writers_stopped=True).status == "complete"
    assert dump(store, ("^InventoryMovement",)) == before_movement
    shell = db.get(Warehouse, "MAIN")
    assert shell.locations == {} and shell.stored_on_hand == 0
    assert BinInventory(db).stock_at(BinAddress("MAIN", "DOCK", "B1"))[0].quantity == 5
    BinInventory(db).move(
        "NEW",
        "LOT-001",
        BinAddress("MAIN", "DOCK", "B1"),
        BinAddress("MAIN", "STORAGE", "B1"),
        Decimal(2),
    )
    for model in (Warehouse, StockBin, StockLocation):
        assert db.check_indexes(model).issues == ()


@pytest.mark.parametrize("checkpoint", ["_copy", "_clear"])
def test_interruption_after_copy_or_clear_leaves_both_layouts_and_cursor_unchanged(
    store, monkeypatch, checkpoint
):
    from examples.inventory import bin_migration

    db = setup(store)
    begin_bin_move(db, writers_stopped=True)
    before = dump(store, ROOTS)
    original = getattr(bin_migration, checkpoint)

    def fail(tx, warehouse):
        original(tx, warehouse)
        raise RuntimeError("Interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(bin_migration, checkpoint, fail)
        with pytest.raises(RuntimeError, match="Interrupted"):
            advance_bin_move(db, writers_stopped=True)
    assert dump(store, ROOTS) == before
    assert finish(store).get(Warehouse, "MAIN").locations == {}


def test_nonempty_target_is_rejected_without_changes(store):
    db = Database(store, Registry(*BIN_MODELS))
    db.insert(Warehouse(id="MAIN", name="Main"))
    create_bin(db, BinAddress("MAIN", "L", "B"))
    before = dump(store, ROOTS)
    with pytest.raises(InventoryError, match="initially empty"):
        begin_bin_move(db, writers_stopped=True)
    assert dump(store, ROOTS) == before


@pytest.mark.native
def test_competing_workers_advance_each_warehouse_once(store):
    import subprocess
    import sys
    from pathlib import Path as FilePath

    from tests.test_native_schema_safety import ready

    db = setup(store)
    db.insert(Warehouse(id="SECOND", name="Second"))
    begin_bin_move(db, writers_stopped=True)
    workers = []
    try:
        for _ in range(2):
            workers.append(
                subprocess.Popen(
                    [sys.executable, "-m", "tests.native_bin_move"],
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
            output, error = worker.communicate("go\n", timeout=30)
            assert worker.returncode == 0 and not error, error
            results.append(output.strip())
        assert results == ["MAIN", "SECOND"]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    assert finish(store).get(Warehouse, "MAIN").locations == {}
