# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic independent-process mutation races."""

import sys
from decimal import Decimal

from mutation_models import Product, Warehouse

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from mccann_hub.yhmf.errors import NotFound, UniqueViolation


class RendezvousStore(NativeStore):
    waiting = True

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == Path("^YHMFTestWarehouse", (b"s:W", b"%m", b"layout")):
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Mutation worker was not released")
        return value


def run(operation, child, value):
    db = Database(RendezvousStore(), Registry(Product, Warehouse))
    at = ("bins", "A", "lots", child)
    try:
        if operation == "delete_parent":
            db.delete(Warehouse, "W")
        else:
            selected = Decimal(value) if operation == "quantity" else value
            db.patch(Warehouse, "W", at=at, changes={operation: selected})
        print("committed", flush=True)
    except (NotFound, UniqueViolation) as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(*sys.argv[1:])
