# SPDX-License-Identifier: GPL-3.0-or-later
"""Pause exact stored-total reads before coordinating conflicting native writers."""

import sys
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, NativeStore, Registry
from mccann_hub.yhmf.errors import NotFound, UniqueViolation
from tests.test_stored_rollups import Warehouse


class UniqueWarehouse(Warehouse):
    total: Decimal = Field(sum_of="bins.items.quantity", unique=True)


class PausedStore(NativeStore):
    waiting = True

    def get(self, path):
        value = super().get(path)
        if (
            self.waiting
            and path.global_name == Warehouse.__global__
            and path.subscripts[-1:] == (b"total",)
        ):
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Rollup worker was not released")
        return value


def run(operation, key, child, quantity):
    model = UniqueWarehouse if operation == "unique" else Warehouse
    db = Database(PausedStore(), Registry(model))
    try:
        if operation == "delete":
            db.delete(model, key)
        else:
            at = (
                ("bins", "X", "items", child)
                if operation == "unique"
                else ("bins", "A", "items", child)
            )
            db.patch(model, key, at=at, changes={"quantity": Decimal(quantity)})
        print("committed", flush=True)
    except (NotFound, UniqueViolation) as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(*sys.argv[1:])
