# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with python -m examples.inventory; --native uses the configured database."""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal

from mccann_hub.yhmf import Database, MemoryStore, NativeStore, Reference, Registry

from . import MODELS, Bin, BinAddress, Inventory, Location, Lot, Product, Warehouse


def seed(db: Database) -> None:
    def install(tx: Database) -> None:
        tx.insert(Product(id="WIDGET", sku="WDG-001", name="Widget"))
        tx.insert(Lot(id="LOT-001", product=Reference[Product](key="WIDGET")))
        tx.insert(
            Warehouse(
                id="MAIN",
                name="Main warehouse",
                locations={
                    name: Location(id=name, bins={"B1": Bin(id="B1")})
                    for name in ("DOCK", "STORAGE")
                },
            )
        )

    db.atomic(install)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    db = Database(NativeStore() if args.native else MemoryStore(), Registry(*MODELS))
    seed(db)
    inventory = Inventory(db)
    dock, storage = BinAddress("MAIN", "DOCK", "B1"), BinAddress("MAIN", "STORAGE", "B1")
    inventory.receive("0001-receive", "LOT-001", dock, Decimal(20))
    inventory.move("0002-move", "LOT-001", dock, storage, Decimal(7))
    inventory.adjust(
        "0003-adjust", "LOT-001", storage, Decimal("-0.5"), reason="Cycle count: damaged stock"
    )
    warehouse = db.get(Warehouse, "MAIN")
    print(
        json.dumps(
            {
                "backend": "native" if args.native else "memory",
                "stock": [asdict(row) for row in inventory.stock_by_product("WIDGET").rows],
                "movements": [movement.id for movement in inventory.history("LOT-001")],
                "warehouse_totals": {
                    "virtual": warehouse.total_on_hand,
                    "stored": warehouse.stored_on_hand,
                },
            },
            default=str,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
