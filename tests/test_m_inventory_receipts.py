# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual stock-plus-history transactions through the downstream M example."""

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, BinAddress, Inventory, Movement, Warehouse
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import Database, MemoryStore, Path, Registry
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_owned_writers import namespaces
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


@pytest.fixture
def receipt(store, m_writer):
    _, run, routine, environment = m_writer
    db = Database(store, Registry(*MODELS))
    seed(db)
    for model, name in ((Warehouse, "WarehouseAPI"), (Movement, "MovementAPI")):
        replace_source(routine.with_name(name + ".m"), db.m_routine(model, name=name))
    source = routine.with_name(SOURCE.name)
    replace_source(source, SOURCE.read_text())
    return db, lambda **data: run("inventoryreceipt", inputs(**data)), source, environment


def receive(db, key="FIRST", quantity="2.5"):
    return Inventory(db).receive(
        key, "LOT-001", ADDRESS, Decimal(quantity), reason="receipt", occurred_at=INSTANT
    )


@pytest.mark.native
@pytest.mark.parametrize("existing", [False, True])
def test_m_receipt_matches_python_and_ordered_history(store, receipt, existing):
    db, run, _, _ = receipt
    expected = Database(MemoryStore(), Registry(*MODELS))
    seed(expected)
    if existing:
        for target in (db, expected):
            receive(target, "OLD", "1")
    assert run() == "ok"
    receive(expected)
    assert dump(store, namespaces()) == dump(expected.store, namespaces())
    assert (
        db.query(Movement).order_by("occurred_at").page(limit=10).records[-1].occurred_at == INSTANT
    )
    assert db.check_indexes(Warehouse).issues == db.check_indexes(Movement).issues == ()


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
    _, run, _, _ = receipt
    before = dump(store, namespaces())
    assert run(**changes) == status
    assert dump(store, namespaces()) == before


@pytest.mark.native
def test_duplicate_movement_rolls_back_balance_and_totals(store, receipt):
    db, run, _, _ = receipt
    receive(db)
    before = dump(store, namespaces())
    assert run(quantity="9") == "exists"
    assert dump(store, namespaces()) == before


@pytest.mark.native
def test_receipt_restarts_after_stock_write_without_double_counting(store, receipt):
    db, run, source, _ = receipt
    marker = " ; Receipt checkpoint:"
    replace_source(
        source, source.read_text().replace(marker, " if $trestart=0 trestart\n" + marker)
    )
    assert run() == "ok"
    assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("2.5")
    assert [m.id for m in Inventory(db).history("LOT-001")] == ["FIRST"]


@pytest.mark.native
def test_competing_receipt_recomputes_stock_after_restart(store, receipt):
    db, run, source, environment = receipt
    # Stage a stable request, then use the same request in a separately coordinated process.
    assert run(quantity="0") == "invalid"
    root = Path("^YHMFTestStorage", (b"writerInput",))
    store.set(root.child(b"quantity"), b"2.5")
    marker = " ; Receipt checkpoint:"
    pause = ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
    replace_source(source, source.read_text().replace(marker, pause + marker))
    worker = subprocess.Popen(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "inventoryreceipt^YHMFWTEST"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        receive(db, "SECOND", "3")
        output, errors = worker.communicate("continue\n", timeout=30)
        assert worker.returncode == 0 and not errors, errors
        assert output.strip() == "ok"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
    assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("5.5")
    assert len(Inventory(db).history("LOT-001")) == 2
    assert db.check_indexes(Warehouse).issues == db.check_indexes(Movement).issues == ()
