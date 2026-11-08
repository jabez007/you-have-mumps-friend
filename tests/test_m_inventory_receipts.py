# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual stock-plus-history transactions through the downstream M example."""

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import BinAddress, Inventory, Movement, Warehouse
from examples.inventory.__main__ import seed
from examples.inventory.bin_migration import advance_bin_move, begin_bin_move
from examples.inventory.bin_models import BIN_MODELS, StockBin, StockMoveState, coordinate_key
from examples.inventory.bins import BinInventory
from mccann_hub.yhmf import Database, MemoryStore, Path, Registry
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_inventory_bins import ROOTS
from tests.test_m_writer_maintenance import replace_source
from tests.test_native_schema_safety import ready

m_writer = test_m_writers.m_writer

SOURCE = FilePath(__file__).parents[1] / "examples/inventory/YHMFRECEIPT.m"
ADDRESS = BinAddress("MAIN", "DOCK", "B1")
INSTANT = datetime(2026, 11, 7, 10, tzinfo=UTC)


def inputs(**changes):
    return (
        dict(
            id="FIRST",
            lot="LOT-001",
            product="WIDGET",
            warehouse="MAIN",
            location="DOCK",
            bin="B1",
            quantity="2.5",
            reason="receipt",
            occurred_at="2026-11-07T10:00:00.000000Z",
        )
        | changes
    )


@pytest.fixture(params=["warehouse", "bin"])
def receipt(store, m_writer, request):
    _, run, routine, environment = m_writer
    layout = request.param
    db = Database(store, Registry(*BIN_MODELS))
    install(db, layout)
    for model, name in (
        (Warehouse, "WarehouseAPI"),
        (Movement, "MovementAPI"),
        (StockBin, "BinAPI"),
        (StockMoveState, "StockMoveAPI"),
    ):
        replace_source(routine.with_name(name + ".m"), db.m_routine(model, name=name))
    source = routine.with_name(SOURCE.name)
    replace_source(source, SOURCE.read_text())
    label = "inventorybinreceipt" if layout == "bin" else "inventoryreceipt"
    return db, lambda **data: run(label, inputs(**data)), source, environment, layout


def install(db, layout):
    seed(db)
    if layout == "bin":
        begin_bin_move(db, writers_stopped=True)
        while advance_bin_move(db, writers_stopped=True).status != "complete":
            pass


def service(db, layout):
    return BinInventory(db) if layout == "bin" else Inventory(db)


def total(db, layout):
    if layout == "bin":
        return db.get(StockBin, coordinate_key("MAIN", "DOCK", "B1")).stored_on_hand
    return db.get(Warehouse, "MAIN").stored_on_hand


def receive(db, layout, key="FIRST", quantity="2.5"):
    return service(db, layout).receive(
        key, "LOT-001", ADDRESS, Decimal(quantity), reason="receipt", occurred_at=INSTANT
    )


@pytest.mark.native
@pytest.mark.parametrize("existing", [False, True])
def test_m_receipt_matches_python_and_ordered_history(store, receipt, existing):
    db, run, _, _, layout = receipt
    expected = Database(MemoryStore(), Registry(*BIN_MODELS))
    install(expected, layout)
    if existing:
        for target in (db, expected):
            receive(target, layout, "OLD", "1")
    assert run() == "ok"
    receive(expected, layout)
    assert dump(store, ROOTS) == dump(expected.store, ROOTS)
    assert (
        db.query(Movement).order_by("occurred_at").page(limit=10).records[-1].occurred_at == INSTANT
    )
    assert (
        db.check_indexes(Warehouse).issues
        == db.check_indexes(Movement).issues
        == db.check_indexes(StockBin).issues
        == ()
    )


@pytest.mark.native
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"quantity": "0"}, "invalid"),
        ({"quantity": "-1"}, "invalid"),
        ({"quantity": "1000000000000"}, "invalid"),
        ({"reason": ""}, "invalid"),
        ({"occurred_at": "bad"}, "invalid"),
        ({"product": "OTHER"}, "references"),
        ({"bin": "MISSING"}, "missing"),
    ],
)
def test_invalid_receipt_rolls_back_stock_and_metadata(store, receipt, changes, status):
    _, run, _, _, layout = receipt
    before = dump(store, ROOTS)
    assert run(**changes) == (
        "references" if layout == "bin" and changes.get("bin") == "MISSING" else status
    )
    assert dump(store, ROOTS) == before


@pytest.mark.native
def test_duplicate_movement_rolls_back_balance_and_totals(store, receipt):
    db, run, _, _, layout = receipt
    receive(db, layout)
    before = dump(store, ROOTS)
    assert run(quantity="9") == "exists"
    assert dump(store, ROOTS) == before


@pytest.mark.native
def test_receipt_restarts_after_stock_write_without_double_counting(store, receipt):
    db, run, source, _, layout = receipt
    marker = " ; Receipt checkpoint:"
    replace_source(
        source, source.read_text().replace(marker, " if $trestart=0 trestart\n" + marker)
    )
    assert run() == "ok"
    assert total(db, layout) == Decimal("2.5")
    assert [m.id for m in service(db, layout).history("LOT-001")] == ["FIRST"]


@pytest.mark.native
def test_competing_receipt_recomputes_stock_after_restart(store, receipt):
    db, run, source, environment, layout = receipt
    # Stage a stable request, then use the same request in a separately coordinated process.
    assert run(quantity="0") == "invalid"
    root = Path("^YHMFTestStorage", (b"writerInput",))
    store.set(root.child(b"quantity"), b"2.5")
    marker = " ; Receipt checkpoint:"
    pause = ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
    replace_source(source, source.read_text().replace(marker, pause + marker))
    worker = subprocess.Popen(
        [
            str(FilePath(environment["ydb_dist"]) / "yottadb"),
            "-run",
            ("inventorybinreceipt" if layout == "bin" else "inventoryreceipt") + "^YHMFWTEST",
        ],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        receive(db, layout, "SECOND", "3")
        output, errors = worker.communicate("continue\n", timeout=30)
        assert worker.returncode == 0 and not errors, errors
        assert output.strip() == "ok"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
    assert total(db, layout) == Decimal("5.5")
    assert len(service(db, layout).history("LOT-001")) == 2
    assert (
        db.check_indexes(Warehouse).issues
        == db.check_indexes(Movement).issues
        == db.check_indexes(StockBin).issues
        == ()
    )


@pytest.mark.native
def test_bin_receipt_respects_the_stock_move_checkpoint(store, receipt):
    db, run, _, _, layout = receipt
    if layout != "bin":
        pytest.skip("The legacy writer must be drained by the move operator")
    db.patch(StockMoveState, "bins-v1", changes={"status": "copying"})
    before = dump(store, ROOTS)
    assert run() == "maintenance"
    assert dump(store, ROOTS) == before


@pytest.mark.native
@pytest.mark.parametrize("status", ["copying", "complete"])
def test_current_legacy_m_receipt_is_fenced_once_the_move_begins(store, receipt, status):
    db, run, _, _, layout = receipt
    if layout != "warehouse":
        pytest.skip("This assertion qualifies the legacy receipt entry")
    db.insert(StockMoveState(id="bins-v1", status=status))
    before = dump(store, ROOTS)
    assert run() == "maintenance"
    assert dump(store, ROOTS) == before
