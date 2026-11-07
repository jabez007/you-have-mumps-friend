# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared workloads and measurements for the disposable native benchmarks."""

import os
import platform
import statistics
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path as FilePath

import pydantic

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.codecs import decode_key, encode_scalar
from mccann_hub.yhmf.references import reference_root


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


class Product(Model):
    __global__ = "^YHMFBenchProduct"
    id: str = Field(primary_key=True)


class TrackedLot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0, index=True)
    serial: str = Field(unique=True)
    product: Reference[Product] = Field(index=True)


class TrackedBin(Model):
    id: str = Field(primary_key=True)
    lots: Children[TrackedLot] = ChildField(key="id")


class TrackedWarehouse(Model):
    __global__ = "^YHMFBenchTracked"
    id: str = Field(primary_key=True)
    bins: Children[TrackedBin] = ChildField(key="id")


WORKLOADS = ("unindexed", "indexed", "unique", "reference")
ROOT_MODELS = (Warehouse, TrackedWarehouse)


def model_for(workload):
    if workload not in WORKLOADS:
        raise ValueError("Unknown benchmark workload")
    return Warehouse if workload == "unindexed" else TrackedWarehouse


def make_warehouse(workload, size, key="W"):
    if workload == "unindexed":
        return Warehouse(
            id=key, bins={"A": Bin(id="A", lots={str(i): Lot(id=str(i)) for i in range(size)})}
        )
    return TrackedWarehouse(
        id=key,
        bins={
            "A": TrackedBin(
                id="A",
                lots={
                    str(i): TrackedLot(
                        id=str(i), serial=f"seed:{key}:{i}", product=Reference[Product](key="P0")
                    )
                    for i in range(size)
                },
            )
        },
    )


def setup(store):
    db = Database(store, Registry(Product, *ROOT_MODELS))
    db.insert(Product(id="P0"))
    db.insert(Product(id="P1"))
    return db


def cleanup(store):
    for model in (Product, *ROOT_MODELS):
        store.delete_tree(Path(model.__global__))
        for name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
            store.delete_tree(Path(name, (model.__global__[1:].encode("ascii"),)))


class CountedStore:
    def __init__(self, store):
        self.store = store
        self.counts = Counter()

    def get(self, path):
        self.counts["get"] += 1
        return self.store.get(path)

    def children(self, path, *, limit, after=b"", reverse=False):
        self.counts["children"] += 1
        return self.store.children(path, limit=limit, after=after, reverse=reverse)

    def set(self, path, value):
        self.counts["set"] += 1
        self.store.set(path, value)

    def delete_tree(self, path):
        self.counts["delete_tree"] += 1
        self.store.delete_tree(path)

    def atomic(self, callback):
        self.counts["atomic"] += 1

        def attempt():
            self.counts["attempts"] += 1
            return callback()

        return self.store.atomic(attempt)


def changes_for(workload, counter, key="W", lot="0"):
    if workload == "unique":
        return {"serial": f"sample:{key}:{lot}:{counter}"}
    if workload == "reference":
        return {"product": Reference[Product](key=f"P{counter % 2}")}
    return {"quantity": Decimal(counter)}


def operation(db, method, workload="unindexed"):
    if method not in ("direct", "patch", "replace") or (
        method == "direct" and workload != "unindexed"
    ):
        raise ValueError("Direct assignment is only comparable for an unindexed workload")
    counter = 0
    model = model_for(workload)
    node = Path(model.__global__, (b"s:W", b"bins", b"s:A", b"lots", b"s:0", b"quantity"))
    at = ("bins", "A", "lots", "0")

    def mutate():
        nonlocal counter
        counter += 1
        changes = changes_for(workload, counter)
        if method == "direct":
            db.store.atomic(lambda: db.store.set(node, encode_scalar(changes["quantity"])))
        elif method == "patch":
            db.patch(model, "W", at=at, changes=changes)
        else:

            def replace(tx):
                root = tx.get(model, "W")
                for name, value in changes.items():
                    setattr(root.bins["A"].lots["0"], name, value)
                tx.save(root)

            db.atomic(replace)

    return mutate


def latencies(elapsed):
    ordered = sorted(elapsed)
    return {
        "median_us": round(statistics.median(elapsed), 2),
        "p95_us": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
    }


def verify_references(db):
    expected = Counter()
    keys = db.store.children(Path(TrackedWarehouse.__global__), limit=10)
    assert len(keys) < 10
    for key in keys:
        root = db.get(TrackedWarehouse, decode_key(key))
        for bin in root.bins.values():
            expected.update(lot.product.key for lot in bin.lots.values())
    for key in ("P0", "P1"):
        target = db.registry.spec(Product).path(key)
        assert int(db.store.get(reference_root(target)) or b"0") == expected[key]


def verify(db, workload, size, counter):
    root = db.get(model_for(workload), "W")
    assert len(root.bins["A"].lots) == size
    for name, expected in changes_for(workload, counter).items():
        assert getattr(root.bins["A"].lots["0"], name) == expected
    assert all(lot.quantity == 0 for key, lot in root.bins["A"].lots.items() if key != "0")
    assert db.check_indexes(model_for(workload), limit=10).issues == ()
    verify_references(db)


def measure(db, size, method, iterations, workload="unindexed"):
    db.save(make_warehouse(workload, size))
    mutate = operation(db, method, workload)
    for _ in range(5):
        mutate()
    db.store.counts.clear()
    elapsed = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        mutate()
        elapsed.append((time.perf_counter_ns() - started) / 1000)
    counts = {name: count / iterations for name, count in sorted(db.store.counts.items())}
    verify(db, workload, size, iterations + 5)
    return {
        "workload": workload,
        "lots": size,
        "method": method,
        "iterations": iterations,
        **latencies(elapsed),
        "operations_per_iteration": counts,
    }


def environment():
    import yottadb

    cpuinfo = FilePath("/proc/cpuinfo")
    cpu = platform.processor()
    if cpuinfo.exists():
        cpu = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text().splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_model": cpu,
        "logical_cpus": os.cpu_count(),
        "pydantic": pydantic.__version__,
        "yottadb": yottadb.release(),
        "source_commit": os.environ.get("GITHUB_SHA"),
        "aggregate_save": "physical_node_difference",
        "journaling": os.environ.get("YHMF_JOURNALING", "0") == "1",
    }
