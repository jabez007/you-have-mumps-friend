# SPDX-License-Identifier: GPL-3.0-or-later
"""Measured public insertions, with an optional rendezvous after the count read."""

import json
import sys
from collections import Counter

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, NativeStore, Registry


class Child(Model):
    id: str = Field(primary_key=True)


class Parent(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    padding: str = ""
    items: Children[Child] = ChildField(key="id")


class TracedStore(NativeStore):
    def __init__(self):
        super().__init__()
        self.counts = Counter()
        self.reads = set()
        self.writes = set()
        self.barrier = None

    def get(self, path):
        self.counts["get"] += 1
        self.reads.add(path)
        value = super().get(path)
        if path == self.barrier:
            self.barrier = None
            rendezvous()
        return value

    def children(self, path, *, limit, after=b""):
        self.counts["children"] += 1
        self.reads.add(path)
        return super().children(path, limit=limit, after=after)

    def set(self, path, value):
        self.counts["set"] += 1
        self.writes.add(path)
        super().set(path, value)

    def atomic(self, callback):
        def attempt():
            self.counts["attempts"] += 1
            return callback()

        return super().atomic(attempt)


def rendezvous():
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Insertion worker was not released")


def paths(values):
    return [
        [path.global_name, [part.decode("ascii") for part in path.subscripts]]
        for path in sorted(values, key=lambda path: (path.global_name, path.subscripts))
    ]


def run(mode, index, key, iterations):
    store = TracedStore()
    db = Database(store, Registry(Parent))
    db.count(Parent, key, collection=("items",))
    store.counts.clear()
    store.reads.clear()
    if mode == "pair":
        store.barrier = db.registry.spec(Parent).path(key).child(b"items")
    else:
        rendezvous()
    for i in range(int(iterations)):
        db.insert_child(Parent, key, collection=("items",), record=Child(id=f"{index}:{i}"))
    print(
        json.dumps(
            {
                "counts": dict(store.counts),
                "reads": paths(store.reads),
                "writes": paths(store.writes),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    run(*sys.argv[1:])
