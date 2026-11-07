# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, BinAddress, Inventory, Lot, Product, Warehouse
from examples.inventory.migrations import (
    WAREHOUSE_TOTALS,
    WAREHOUSE_TOTALS_V3,
    LegacyWarehouse,
    WarehouseV2,
)
from mccann_hub.yhmf import Database, Registry
from mccann_hub.yhmf.errors import SchemaMismatch
from tests.adversarial_support import dump
from tests.test_inventory_warehouse_migration import legacy_database


def test_location_total_schema_has_its_own_golden():
    fixture = FilePath(__file__).with_name("fixtures") / "inventory-location-totals-v3.json"
    assert Registry(*MODELS).spec(Warehouse).snapshot == fixture.read_bytes().rstrip(b"\n")


@pytest.mark.parametrize("phase", ["backfill", "retag", "activate"])
def test_frozen_v2_transform_can_resume_before_explicit_owned_total_upgrade(store, phase):
    legacy_database(store)
    old = Database(store, Registry(Product, Lot, WarehouseV2))
    status = old.begin_migration(
        LegacyWarehouse,
        WarehouseV2,
        plan=old.plan_migration(WarehouseV2, transform=WAREHOUSE_TOTALS),
        transform=WAREHOUSE_TOTALS,
    )
    for _ in range(100):
        if status.phase == phase:
            break
        status = old.advance_migration(
            status.migration_id,
            LegacyWarehouse,
            WarehouseV2,
            transform=WAREHOUSE_TOTALS,
            batch_size=1,
        )
    assert status.phase == phase
    # Recreate the registry/handle, preserving the historical named transform.
    for _ in range(100):
        if status.phase == "complete":
            break
        old = Database(store, Registry(Product, Lot, WarehouseV2))
        status = old.advance_migration(
            status.migration_id,
            LegacyWarehouse,
            WarehouseV2,
            transform=WAREHOUSE_TOTALS,
            batch_size=1,
        )
    assert status.phase == "complete"
    assert old.get(WarehouseV2, "MAIN").stored_on_hand == Decimal("12.5")
    references = dump(store, ("^YHMFRef",))
    db = Database(store, Registry(*MODELS))
    with pytest.raises(SchemaMismatch):
        db.get(Warehouse, "MAIN")
    assert not db.plan_migration(Warehouse).execution_supported
    plan = db.plan_migration(Warehouse, transform=WAREHOUSE_TOTALS_V3)
    status = db.begin_migration(WarehouseV2, Warehouse, plan=plan, transform=WAREHOUSE_TOTALS_V3)
    for _ in range(100):
        if status.phase == "complete":
            break
        db = Database(store, Registry(*MODELS))
        status = db.advance_migration(
            status.migration_id, WarehouseV2, Warehouse, transform=WAREHOUSE_TOTALS_V3, batch_size=1
        )
    assert status.phase == "complete"
    assert dump(store, ("^YHMFRef",)) == references
    root = db.get(Warehouse, "MAIN")
    assert root.stored_on_hand == root.locations["DOCK"].stored_on_hand == Decimal("12.5")
    assert root.locations["STORAGE"].stored_on_hand == 0
    Inventory(db).move(
        "M",
        "LOT-001",
        BinAddress("MAIN", "DOCK", "B1"),
        BinAddress("MAIN", "STORAGE", "B1"),
        Decimal(2),
    )
    root = db.get(Warehouse, "MAIN")
    assert root.stored_on_hand == Decimal("12.5")
    assert root.locations["DOCK"].stored_on_hand == Decimal("10.5")
    assert root.locations["STORAGE"].stored_on_hand == 2
    assert db.query(Warehouse).where(**{"locations.stored_on_hand": Decimal(2)}).occurrences()[
        0
    ].at == ("locations", "STORAGE")
    assert db.check_indexes(Warehouse).issues == ()
