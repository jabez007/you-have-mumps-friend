# SPDX-License-Identifier: GPL-3.0-or-later
"""Bin aggregate boundaries preserve inventory behavior and bounded reporting."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from examples.inventory import Balance, BinAddress, Lot, Product, Warehouse
from examples.inventory.bin_models import BIN_MODELS, StockBin, StockLocation, coordinate_key
from examples.inventory.bins import BinInventory, create_bin
from examples.inventory.ledger import InsufficientStock
from mccann_hub.yhmf import Database, MemoryStore, Reference, Registry
from mccann_hub.yhmf.errors import AlreadyExists, ReferenceViolation
from scripts.benchmark_support import CountedStore
from tests.adversarial_support import dump

DOCK = BinAddress("MAIN", "DOCK", "B1")
STORAGE = BinAddress("MAIN", "STORAGE", "B1")
STAMP = datetime(2026, 11, 7, tzinfo=UTC)
ROOTS = tuple(model.__global__ for model in BIN_MODELS) + (
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
)


def setup(store):
    db = Database(store, Registry(*BIN_MODELS))
    db.insert(Product(id="P", sku="SKU", name="Product"))
    db.insert(Lot(id="LOT", product=Reference[Product](key="P")))
    db.insert(Warehouse(id="MAIN", name="Main"))
    for address in (DOCK, STORAGE):
        create_bin(db, address)
    return db, BinInventory(db)


def test_bin_inventory_workflows_ledger_queries_and_rollback(store):
    db, inventory = setup(store)
    inventory.receive("R", "LOT", DOCK, Decimal(20), occurred_at=STAMP)
    inventory.move("M", "LOT", DOCK, STORAGE, Decimal(7), occurred_at=STAMP)
    inventory.adjust("A", "LOT", STORAGE, Decimal("-0.5"), reason="Count", occurred_at=STAMP)
    assert inventory.stock_at(DOCK)[0].quantity == 13
    assert inventory.stock_at(STORAGE)[0].quantity == Decimal("6.5")
    page = inventory.stock_by_product("P", bin_limit=1)
    rest = inventory.stock_by_product("P", bin_limit=1, after=page.next_bin)
    assert {r.address for r in page.rows + rest.rows} == {DOCK, STORAGE}
    assert len(inventory.stock_in_warehouse("MAIN", location="DOCK").rows) == 1
    assert {m.id for m in inventory.history("LOT")} == {"R", "M", "A"}
    before = dump(store, ROOTS)
    with pytest.raises(AlreadyExists):
        inventory.receive("R", "LOT", DOCK, Decimal(2), occurred_at=STAMP)
    with pytest.raises(InsufficientStock):
        inventory.move("NO", "LOT", DOCK, STORAGE, Decimal(100), occurred_at=STAMP)
    assert dump(store, ROOTS) == before
    assert db.get(Warehouse, "MAIN").locations == {}
    for model in (StockBin, StockLocation):
        assert db.check_indexes(model).issues == ()


def test_root_references_protect_operational_lifetimes(store):
    db, _ = setup(store)
    with pytest.raises(ReferenceViolation):
        db.delete(StockLocation, coordinate_key("MAIN", "DOCK"))
    with pytest.raises(ReferenceViolation):
        db.delete(Warehouse, "MAIN")
    db.delete(StockBin, coordinate_key("MAIN", "DOCK", "B1"))
    db.delete(StockLocation, coordinate_key("MAIN", "DOCK"))


def test_coordinate_identity_is_injective_for_delimiters_and_unicode():
    assert coordinate_key("a:", "b") != coordinate_key("a", ":b")
    assert coordinate_key("a", "b", "c") != coordinate_key("a", "bc")
    assert coordinate_key("雪", "x") == "3:雪1:x"


def test_insert_and_delete_work_does_not_grow_with_unrelated_bins():
    counted = CountedStore(MemoryStore())
    db, _ = setup(counted)
    value = Balance(
        id="LOT",
        lot=Reference[Lot](key="LOT"),
        product=Reference[Product](key="P"),
        quantity=Decimal(1),
    )

    def measure():
        counted.counts.clear()
        db.insert_child(
            StockBin, coordinate_key("MAIN", "DOCK", "B1"), collection=("balances",), record=value
        )
        db.delete_child(StockBin, coordinate_key("MAIN", "DOCK", "B1"), at=("balances", "LOT"))
        return dict(counted.counts)

    small = measure()
    for i in range(998):
        create_bin(db, BinAddress("MAIN", "DOCK", f"EXTRA-{i}"))
    assert measure() == small


def test_warehouse_stock_can_exceed_the_old_aggregate_budget(store):
    db, inventory = setup(store)

    def fill(tx):
        for i in range(1600):
            tx.insert(Lot(id=f"LOT-{i}", product=Reference[Product](key="P")))
        for start, address in ((0, DOCK), (800, STORAGE)):
            key = coordinate_key(address.warehouse, address.location, address.bin)
            bin_ = tx.get(StockBin, key)
            balances = {
                f"LOT-{i}": Balance(
                    id=f"LOT-{i}",
                    lot=Reference[Lot](key=f"LOT-{i}"),
                    product=Reference[Product](key="P"),
                    quantity=Decimal(1),
                )
                for i in range(start, start + 800)
            }
            tx.save(bin_.model_copy(update={"balances": balances}))

    db.atomic(fill)
    assert len(inventory.stock_in_warehouse("MAIN").rows) == 1600
    assert sum(r.quantity for r in inventory.stock_by_product("P").rows) == 1600
    assert db.check_indexes(StockBin).issues == ()
