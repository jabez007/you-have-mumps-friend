# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic independent-process mutation races."""

import sys
from decimal import Decimal

from mutation_models import Lot, Product, Warehouse

from mccann_hub.yhmf import Database, NativeStore, Patch, Path, Reference, Registry
from mccann_hub.yhmf.errors import NotFound, ReferenceViolation, UniqueViolation


class RendezvousStore(NativeStore):
    waiting = True

    def __init__(self, operation):
        super().__init__()
        self.guard = Path("^YHMFTestWarehouse", (b"s:W", b"%m", b"layout"))
        if operation in {"link_product", "delete_product", "delete_referenced_child"}:
            self.guard = Path("^YHMFRef", (b"YHMFTestProduct", b"s:P"))

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == self.guard:
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Mutation worker was not released")
        return value


def run(operation, child, value):
    db = Database(RendezvousStore(operation), Registry(Product, Warehouse))
    at = ("bins", "A", "lots", child)
    try:
        if operation == "delete_parent":
            db.delete(Warehouse, "W")
        elif operation in {"delete_child", "delete_referenced_child"}:
            db.delete_child(Warehouse, "W", at=at)
        elif operation == "delete_bin":
            db.delete_child(Warehouse, "W", at=("bins", "A"))
        elif operation == "insert":
            db.insert_child(
                Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id=child, note=value)
            )
        elif operation == "link_product":
            db.patch(Warehouse, "W", at=at, changes={"product": Reference[Product](key="P")})
        elif operation == "delete_product":
            db.delete(Product, "P")
        elif operation == "swap":
            db.patch_many(
                [
                    Patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"serial": "B"}),
                    Patch(Warehouse, "W", at=("bins", "A", "lots", "1"), changes={"serial": "A"}),
                ]
            )
        else:
            selected = Decimal(value) if operation == "quantity" else value
            db.patch(Warehouse, "W", at=at, changes={operation: selected})
        print("committed", flush=True)
    except (NotFound, UniqueViolation, ReferenceViolation) as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(*sys.argv[1:])
