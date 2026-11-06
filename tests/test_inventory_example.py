# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the example as a downstream client on both adapters."""

from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError

from examples.inventory import (
    MODELS,
    Bin,
    BinAddress,
    InsufficientStock,
    Inventory,
    InventoryError,
    Location,
    Lot,
    Product,
    Warehouse,
)
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import Database, Path, Reference, Registry
from mccann_hub.yhmf.errors import AlreadyExists, NotFound, ReferenceViolation
from tests.adversarial_support import dump

DOCK, STORAGE = BinAddress("MAIN", "DOCK", "B1"), BinAddress("MAIN", "STORAGE", "B1")
TIME = "2026-11-06T12:00:00+00:00"
ROOTS = tuple(model.__global__ for model in MODELS) + (
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
)


@pytest.fixture
def inventory(store):
    db = Database(store, Registry(*MODELS))
    seed(db)
    return Inventory(db)


def test_receive_move_adjust_and_query_audited_inventory(inventory):
    received = inventory.receive("01", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    inventory.move("02", "LOT-001", DOCK, STORAGE, Decimal(7), occurred_at=TIME)
    inventory.adjust(
        "03", "LOT-001", STORAGE, Decimal("-0.5"), reason="Cycle count damage", occurred_at=TIME
    )
    assert [row.quantity for row in inventory.stock_in_warehouse("MAIN")] == [
        Decimal(13),
        Decimal("6.5"),
    ]
    assert inventory.stock_at(STORAGE)[0].quantity == Decimal("6.5")
    assert inventory.stock_in_warehouse("MAIN", location="DOCK")[0].quantity == 13
    page = inventory.stock_by_product("WIDGET")
    assert len(page.rows) == 2 and page.next_warehouse is None
    history = inventory.history("LOT-001")
    assert [value.kind for value in history] == ["receive", "move", "adjust"]
    assert history[1].source.location == "DOCK"
    assert history[2].quantity == Decimal("-0.5")
    assert received.occurred_at == TIME
    for model in MODELS:
        assert inventory.db.check_indexes(model).issues == ()
    with pytest.raises(ValidationError):
        received.reason = "rewrite history"
    assert (
        inventory.db.store.get(
            Path(
                "^InventoryWarehouse",
                (
                    b"s:MAIN",
                    b"locations",
                    b"s:STORAGE",
                    b"bins",
                    b"s:B1",
                    b"balances",
                    b"s:LOT-001",
                    b"quantity",
                ),
            )
        )
        == b"d:6.5"
    )


def test_duplicate_ledger_id_rolls_back_balance_and_index_changes(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(AlreadyExists):
        inventory.move("01", "LOT-001", DOCK, STORAGE, Decimal(7), occurred_at=TIME)
    assert dump(inventory.db.store, ROOTS) == before


def test_missing_destination_rolls_back_debit_and_ledger(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(NotFound):
        inventory.move(
            "02", "LOT-001", DOCK, BinAddress("MAIN", "missing", "B1"), Decimal(7), occurred_at=TIME
        )
    assert dump(inventory.db.store, ROOTS) == before


def test_insufficient_stock_and_unjustified_adjustments_do_not_write(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal(2), occurred_at=TIME)
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(InsufficientStock):
        inventory.move("02", "LOT-001", DOCK, STORAGE, Decimal(3), occurred_at=TIME)
    with pytest.raises(InventoryError, match="reason"):
        inventory.adjust("03", "LOT-001", DOCK, Decimal(-1), reason=" ", occurred_at=TIME)
    with pytest.raises(InsufficientStock):
        inventory.adjust("04", "LOT-001", DOCK, Decimal(-3), reason="count", occurred_at=TIME)
    with pytest.raises(InventoryError, match="differ"):
        inventory.move("05", "LOT-001", DOCK, DOCK, Decimal(1), occurred_at=TIME)
    assert dump(inventory.db.store, ROOTS) == before


@pytest.mark.parametrize(
    "value",
    [
        Decimal(0),
        Decimal(-1),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("0.0000001"),
        Decimal("1000000000000000000"),
        1,
        "1",
    ],
)
def test_invalid_receipts_fail_before_writing(inventory, value):
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(InventoryError):
        inventory.receive("01", "LOT-001", DOCK, value, occurred_at=TIME)
    assert dump(inventory.db.store, ROOTS) == before


def test_history_and_catalog_references_restrict_deletion(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal(1), occurred_at=TIME)
    for model, key in ((Product, "WIDGET"), (Lot, "LOT-001"), (Warehouse, "MAIN")):
        with pytest.raises(ReferenceViolation):
            inventory.db.delete(model, key)


def test_product_index_pages_warehouses_and_history_pages_movements(inventory):
    inventory.db.insert(
        Warehouse(
            id="SECOND",
            name="Second",
            locations={"STORAGE": Location(id="STORAGE", bins={"B1": Bin(id="B1")})},
        )
    )
    other = BinAddress("SECOND", "STORAGE", "B1")
    inventory.receive("01", "LOT-001", DOCK, Decimal(10), occurred_at=TIME)
    inventory.move("02", "LOT-001", DOCK, other, Decimal(4), occurred_at=TIME)
    first = inventory.stock_by_product("WIDGET", warehouse_limit=1)
    second = inventory.stock_by_product("WIDGET", warehouse_limit=1, after=first.next_warehouse)
    assert first.rows[0].address.warehouse == "MAIN"
    assert second.rows[0].address.warehouse == "SECOND"
    assert (
        inventory.stock_by_product("WIDGET", warehouse_limit=1, after=second.next_warehouse).rows
        == ()
    )
    assert [entry.id for entry in inventory.history("LOT-001", limit=1, after="01")] == ["02"]
    assert inventory.stock_by_product("unknown").rows == ()


def test_tampered_product_association_is_rejected(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal(2), occurred_at=TIME)
    inventory.db.insert(Product(id="OTHER", sku="OTHER", name="Other"))
    inventory.db.patch(
        Warehouse,
        "MAIN",
        at=DOCK.balance_path("LOT-001"),
        changes={"product": Reference[Product](key="OTHER")},
    )
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(InventoryError, match="does not match"):
        inventory.adjust("02", "LOT-001", DOCK, Decimal(1), reason="count", occurred_at=TIME)
    assert dump(inventory.db.store, ROOTS) == before


def test_exact_movement_arithmetic_does_not_depend_on_callers_decimal_context(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal("12.345678"), occurred_at=TIME)
    with localcontext() as context:
        context.prec = 2
        inventory.move("02", "LOT-001", DOCK, STORAGE, Decimal("1.234567"), occurred_at=TIME)
    assert inventory.stock_at(DOCK)[0].quantity == Decimal("11.111111")
    assert inventory.stock_at(STORAGE)[0].quantity == Decimal("1.234567")


def test_new_balance_cannot_exceed_the_inventory_quantity_domain(inventory):
    inventory.receive("01", "LOT-001", DOCK, Decimal("999999999999.999999"), occurred_at=TIME)
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(ValidationError):
        inventory.receive("02", "LOT-001", DOCK, Decimal(1), occurred_at=TIME)
    assert dump(inventory.db.store, ROOTS) == before


@pytest.mark.parametrize("timestamp", ["invalid", "2026-11-06T12:00:00"])
def test_timestamp_errors_are_clear_and_leave_state_unchanged(inventory, timestamp):
    before = dump(inventory.db.store, ROOTS)
    with pytest.raises(InventoryError, match="time"):
        inventory.receive("01", "LOT-001", DOCK, Decimal(1), occurred_at=timestamp)
    assert dump(inventory.db.store, ROOTS) == before


def test_ledger_index_failure_rolls_back_balances_references_and_metadata(inventory, monkeypatch):
    store = inventory.db.store
    before, original = dump(store, ROOTS), store.set
    tripped = []

    def fail(path, value):
        original(path, value)
        if path.global_name == "^YHMFIndex" and path.subscripts[0] == b"InventoryMovement":
            tripped.append(path)
            raise RuntimeError("ledger index failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", fail)
        with pytest.raises(RuntimeError, match="ledger index"):
            inventory.receive("01", "LOT-001", DOCK, Decimal(20), occurred_at=TIME)
    assert tripped
    assert dump(store, ROOTS) == before
