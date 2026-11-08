# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinate two checkpoint readers before either copy/clear can commit."""

import sys

from examples.inventory.bin_migration import advance_bin_move
from examples.inventory.bin_models import BIN_MODELS, StockMoveState
from mccann_hub.yhmf import Database, NativeStore, Path, Registry


class RendezvousStore(NativeStore):
    waiting = True

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == Path(StockMoveState.__global__, (b"s:bins-v1", b"after")):
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Move worker was not released")
        return value


if __name__ == "__main__":
    db = Database(RendezvousStore(), Registry(*BIN_MODELS))
    print(advance_bin_move(db, writers_stopped=True).after, flush=True)
