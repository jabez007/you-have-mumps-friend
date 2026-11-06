# SPDX-License-Identifier: GPL-3.0-or-later
"""Descriptive native mutation benchmark; requires the disposable test database."""

import argparse
import json
import os
import platform
import statistics
import time
from collections import Counter
from decimal import Decimal

import pydantic

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    NativeStore,
    Path,
    Registry,
)
from mccann_hub.yhmf.codecs import encode_scalar


class Lot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)


class Bin(Model):
    id: str = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="id")


class Warehouse(Model):
    __global__ = "^YHMFBenchWarehouse"
    id: str = Field(primary_key=True)
    bins: Children[Bin] = ChildField(key="id")


class CountedStore:
    def __init__(self, store):
        self.store = store
        self.counts = Counter()

    def get(self, path):
        self.counts["get"] += 1
        return self.store.get(path)

    def children(self, path, *, limit, after=b""):
        self.counts["children"] += 1
        return self.store.children(path, limit=limit, after=after)

    def set(self, path, value):
        self.counts["set"] += 1
        self.store.set(path, value)

    def delete_tree(self, path):
        self.counts["delete_tree"] += 1
        self.store.delete_tree(path)

    def atomic(self, callback):
        self.counts["atomic"] += 1
        return self.store.atomic(callback)


def operation(db, method):
    counter = 0
    node = Path(Warehouse.__global__, (b"s:W", b"bins", b"s:A", b"lots", b"s:0", b"quantity"))
    at = ("bins", "A", "lots", "0")

    def mutate():
        nonlocal counter
        counter += 1
        value = Decimal(counter)
        if method == "direct":
            db.store.atomic(lambda: db.store.set(node, encode_scalar(value)))
        elif method == "patch":
            db.patch(Warehouse, "W", at=at, changes={"quantity": value})
        else:

            def replace(tx):
                root = tx.get(Warehouse, "W")
                root.bins["A"].lots["0"].quantity = value
                tx.save(root)

            db.atomic(replace)

    return mutate


def measure(db, size, method, iterations):
    db.save(
        Warehouse(
            id="W", bins={"A": Bin(id="A", lots={str(i): Lot(id=str(i)) for i in range(size)})}
        )
    )
    mutate = operation(db, method)
    for _ in range(5):
        mutate()
    db.store.counts.clear()
    elapsed = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        mutate()
        elapsed.append((time.perf_counter_ns() - started) / 1000)
    counts = {name: count / iterations for name, count in sorted(db.store.counts.items())}
    assert db.get(Warehouse, "W").bins["A"].lots["0"].quantity == iterations + 5
    ordered = sorted(elapsed)
    return {
        "lots": size,
        "method": method,
        "iterations": iterations,
        "median_us": round(statistics.median(elapsed), 2),
        "p95_us": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
        "operations_per_iteration": counts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 10, 100, 1000])
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Run only inside scripts/test-native.sh with YHMF_BENCHMARK=1")
    if args.iterations < 10 or any(not 1 <= size <= 1000 for size in args.sizes):
        parser.error("Use at least 10 iterations and sizes from 1 to 1000")
    store = CountedStore(NativeStore())
    db = Database(store, Registry(Warehouse))
    root = Path(Warehouse.__global__)
    schema = Path("^YHMFSchema", (b"YHMFBenchWarehouse",))
    store.delete_tree(root)
    store.delete_tree(schema)
    try:
        rows = [
            measure(db, size, method, args.iterations)
            for size in args.sizes
            for method in ("direct", "patch", "replace")
        ]
        import yottadb

        print(
            "YHMF_BENCHMARK="
            + json.dumps(
                {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "pydantic": pydantic.__version__,
                    "yottadb": yottadb.release(),
                    "warmup_iterations": 5,
                    "indexed_quantity": False,
                    "rows": rows,
                },
                sort_keys=True,
            )
        )
    finally:
        store.delete_tree(root)
        store.delete_tree(schema)


if __name__ == "__main__":
    main()
