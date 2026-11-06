# SPDX-License-Identifier: GPL-3.0-or-later
"""Controlled competition for the same inventory balance."""

import sys
from decimal import Decimal

from examples.inventory import MODELS, BinAddress, InsufficientStock, Inventory, Warehouse
from mccann_hub.yhmf import Database, NativeStore, Path, Registry


class RendezvousStore(NativeStore):
    waiting = True
    guard = Path(
        Warehouse.__global__,
        (
            b"s:MAIN",
            b"locations",
            b"s:DOCK",
            b"bins",
            b"s:B1",
            b"balances",
            b"s:LOT-001",
            b"quantity",
        ),
    )

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == self.guard:
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Inventory worker was not released")
        return value


def run(movement_id):
    inventory = Inventory(Database(RendezvousStore(), Registry(*MODELS)))
    try:
        inventory.move(
            movement_id,
            "LOT-001",
            BinAddress("MAIN", "DOCK", "B1"),
            BinAddress("MAIN", "STORAGE", "B1"),
            Decimal(15),
            occurred_at="2026-11-06T12:00:00+00:00",
        )
    except InsufficientStock:
        print("InsufficientStock", flush=True)
    else:
        print("committed", flush=True)


if __name__ == "__main__":
    run(sys.argv[1])
