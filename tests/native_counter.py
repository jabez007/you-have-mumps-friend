# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared native test model and a worker launched in independent Python processes."""

import json
import sys
import time

from mccann_hub.yhmf import Database, Field, Model, NativeStore, Registry


class Counter(Model):
    __global__ = "^YHMFTestCounter"
    id: int = Field(primary_key=True)
    count: int = Field(default=0, ge=0)
    mirror: int = Field(default=0, ge=0)


def run(iterations: int) -> None:
    db = Database(NativeStore(), Registry(Counter))
    attempts = 0

    def increment(tx: Database) -> None:
        nonlocal attempts
        # This counter and delay are test instrumentation for native conflicts.
        attempts += 1
        counter = tx.get(Counter, 1)
        time.sleep(0.001)
        counter.count += 1
        counter.mirror = counter.count
        tx.save(counter)

    for _ in range(iterations):
        db.atomic(increment)
    print(json.dumps({"commits": iterations, "attempts": attempts}))


if __name__ == "__main__":
    run(int(sys.argv[1]))
