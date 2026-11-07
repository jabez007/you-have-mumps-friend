# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinate stale root-total reads while owned sums also participate."""

import sys
from decimal import Decimal

from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.errors import NotFound
from tests.test_owned_rollups import Warehouse


class PausedStore(NativeStore):
    waiting = True

    def get(self, path):
        value = super().get(path)
        if (
            self.waiting
            and path.global_name == Warehouse.__global__
            and path.subscripts == (b"s:W", b"total")
        ):
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Owned sum worker was not released")
        return value


def run(operation, location, child, quantity):
    db = Database(PausedStore(), Registry(Warehouse))
    try:
        if operation == "delete":
            db.delete_child(Warehouse, "W", at=("locations", location))
        else:
            db.patch(
                Warehouse,
                "W",
                at=("locations", location, "items", child),
                changes={"quantity": Decimal(quantity)},
            )
        print("committed", flush=True)
    except NotFound:
        print("NotFound", flush=True)


if __name__ == "__main__":
    run(*sys.argv[1:])
