# SPDX-License-Identifier: GPL-3.0-or-later
"""Pause first gate reads to coordinate adoption with another initializer."""

import sys

from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.errors import YHMFError
from mccann_hub.yhmf.maintenance import GATE
from tests.test_adoption import Product


class PausedStore(NativeStore):
    waiting = True

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == GATE:
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Initializer was not released")
        return value


def run(operation):
    db = Database(PausedStore(), Registry(Product))
    try:
        if operation == "adopt":
            status = db.begin_adoption(Product)
            print("job:" + status.migration_id, flush=True)
        else:
            db.insert(Product(id="managed", name="Managed"))
            print("inserted", flush=True)
    except YHMFError as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(sys.argv[1])
