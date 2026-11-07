# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, BinAddress, Inventory, Lot, Movement, Product, Warehouse
from examples.inventory.__main__ import seed
from examples.inventory.migrations import (
    WAREHOUSE_TOTALS_V3 as WAREHOUSE_TOTALS,
)
from examples.inventory.migrations import (
    LegacyBalance,
    LegacyBin,
    LegacyLocation,
    LegacyWarehouse,
    WarehouseV2,
)
from mccann_hub.yhmf import Database, Field, Model, Reference, Registry
from mccann_hub.yhmf.errors import SchemaMismatch
from mccann_hub.yhmf.storage import subtree_nodes
from tests.adversarial_support import dump
from tests.test_partial_mutations import ObservedStore


class BeforeReferrer(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)
    warehouse: Reference[LegacyWarehouse]


class CurrentReferrer(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)
    warehouse: Reference[Warehouse]


def legacy_database(store):
    models = tuple(model for model in MODELS if model not in (Warehouse, Movement))
    old = Database(store, Registry(*models, LegacyWarehouse, BeforeReferrer))
    old.insert(Product(id="WIDGET", sku="WIDGET", name="Widget"))
    old.insert(Lot(id="LOT-001", product=Reference[Product](key="WIDGET")))
    old.insert(
        LegacyWarehouse(
            id="MAIN",
            name="Main",
            locations={
                "DOCK": LegacyLocation(
                    id="DOCK",
                    bins={
                        "B1": LegacyBin(
                            id="B1",
                            balances={
                                "LOT-001": LegacyBalance(
                                    id="LOT-001",
                                    lot=Reference[Lot](key="LOT-001"),
                                    product=Reference[Product](key="WIDGET"),
                                    quantity=Decimal("12.5"),
                                )
                            },
                        )
                    },
                ),
                "STORAGE": LegacyLocation(id="STORAGE", bins={"B1": LegacyBin(id="B1")}),
            },
        )
    )
    old.insert(BeforeReferrer(id="R", warehouse=Reference[LegacyWarehouse](key="MAIN")))
    return old


def current(store):
    return Database(store, Registry(*MODELS, CurrentReferrer))


def advance(db, status):
    return db.advance_migration(
        status.migration_id, LegacyWarehouse, Warehouse, transform=WAREHOUSE_TOTALS, batch_size=1
    )


def finish(store, status):
    for _ in range(200):
        if status.phase == "complete":
            return status
        status = advance(current(store), status)
    raise AssertionError("Warehouse total migration did not complete")


def test_warehouse_total_revision_has_independent_golden_and_preserves_reference_contracts():
    golden = FilePath(__file__).with_name("fixtures") / "inventory-warehouse-total-v2.json"
    assert Registry(WarehouseV2, Product, Lot).spec(
        WarehouseV2
    ).snapshot == golden.read_bytes().rstrip(b"\n")
    assert (
        Registry().register(BeforeReferrer).snapshot
        == Registry().register(CurrentReferrer).snapshot
    )


@pytest.mark.parametrize("phase", ["backfill", "retag", "activate"])
def test_existing_warehouse_upgrade_is_explicit_resumable_and_preserves_stock_and_incoming_edges(
    store, monkeypatch, phase
):
    old = legacy_database(store)
    db = current(store)
    before_data = subtree_nodes(store, db.registry.spec(Warehouse).path("MAIN"), limit=10000)
    before_fields = {
        path: value for path, value in before_data.items() if path.subscripts[1] != b"%m"
    }
    before_refs = dump(store, ("^YHMFRef",))
    assert old.get(LegacyWarehouse, "MAIN").locations["DOCK"].bins["B1"].balances[
        "LOT-001"
    ].quantity == Decimal("12.5")
    with pytest.raises(SchemaMismatch):
        db.get(Warehouse, "MAIN")
    assert not db.plan_migration(Warehouse).execution_supported
    status = db.begin_migration(
        LegacyWarehouse,
        Warehouse,
        plan=db.plan_migration(Warehouse, transform=WAREHOUSE_TOTALS),
        transform=WAREHOUSE_TOTALS,
    )
    for _ in range(200):
        if status.phase == phase:
            break
        status = advance(current(store), status)
    assert status.phase == phase
    roots = (
        "^InventoryWarehouse",
        "^YHMFSchema",
        "^YHMFRef",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFMigration",
    )
    before = dump(store, roots)
    original = store.set

    def interrupt(path, value):
        original(path, value)
        raise RuntimeError("warehouse migration interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", interrupt)
        with pytest.raises(RuntimeError):
            advance(db, status)
    assert dump(store, roots) == before
    assert finish(store, status).phase == "complete"
    value = db.get(Warehouse, "MAIN")
    assert value.stored_on_hand == value.total_on_hand == Decimal("12.5")
    after = subtree_nodes(store, db.registry.spec(Warehouse).path("MAIN"), limit=10000)
    assert {
        path: content
        for path, content in after.items()
        if path.subscripts[1] != b"%m" and path.subscripts[-1] != b"stored_on_hand"
    } == before_fields
    assert dump(store, ("^YHMFRef",)) == before_refs
    assert len(db.referrers(Reference[Warehouse](key="MAIN"), source=CurrentReferrer)) == 1
    assert db.check_indexes(Warehouse).issues == ()
    assert db.query(Warehouse).where(stored_on_hand=Decimal("12.5")).all()[0].id == "MAIN"
    Inventory(db).receive("R1", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal(".5"))
    assert db.get(Warehouse, "MAIN").stored_on_hand == 13


def test_inventory_existing_balance_moves_group_net_zero_rollups_without_total_io(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(*MODELS))
    seed(db)
    inventory = Inventory(db)
    dock, storage = BinAddress("MAIN", "DOCK", "B1"), BinAddress("MAIN", "STORAGE", "B1")
    inventory.receive("R1", "LOT-001", dock, Decimal(20))
    inventory.move("M1", "LOT-001", dock, storage, Decimal(5))
    observed.operations.clear()
    inventory.move("M2", "LOT-001", dock, storage, Decimal(2))
    total = db.registry.spec(Warehouse).path("MAIN").child(b"stored_on_hand")
    assert not any(
        path == total
        or path.global_name == "^YHMFIndex"
        and path.subscripts[1] == b"stored_on_hand"
        for _, path in observed.operations
    )
    assert db.get(Warehouse, "MAIN").stored_on_hand == db.get(Warehouse, "MAIN").total_on_hand == 20
    assert db.check_indexes(Warehouse).issues == ()
