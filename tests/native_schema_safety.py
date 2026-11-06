# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent-process schema installation and detached-write rendezvous."""

import sys

from mccann_hub.yhmf import Database, Field, Model, NativeStore, Path, Registry


class Counter(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    count: int = 0


def wait():
    # Test instrumentation only; production transaction callbacks must avoid IO.
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Worker was not released")


class RendezvousStore(NativeStore):
    waiting = True

    def get(self, path):
        result = super().get(path)
        if self.waiting and path == Path("^YHMFSchema", (b"YHMFTestProduct",)):
            self.waiting = False
            wait()
        return result


def run(operation, key):
    if operation == "detached":
        db = Database(NativeStore(), Registry(Counter))
        record = db.get(Counter, 1)
        wait()
        record.count += 1
        db.save(record)
    else:
        db = Database(RendezvousStore(), Registry(Counter))
        if operation == "install":
            db.insert(Counter(id=key))
        else:

            def increment(tx):
                record = tx.get(Counter, 1)
                record.count += 1
                tx.save(record)

            db.atomic(increment)
    print("committed", flush=True)


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]))
