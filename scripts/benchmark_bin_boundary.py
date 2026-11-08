# SPDX-License-Identifier: GPL-3.0-or-later
"""Native 2/1000-bin controls; run only inside the disposable native harness."""

import argparse
import json
import os
import subprocess
import tempfile
import time
from decimal import Decimal
from pathlib import Path as FilePath

from examples.inventory import Balance, Bin, BinAddress, Location, Lot, Product, Warehouse
from examples.inventory.bin_models import BIN_MODELS, StockBin, coordinate_key
from examples.inventory.bins import create_bin
from mccann_hub.yhmf import Database, Field, NativeStore, Path, Reference, Registry
from scripts.benchmark_support import CountedStore, environment, latencies


class FlatBalance(Balance):
    __global__ = "^InventoryBenchBalance"
    position: Reference[StockBin] = Field(index=True)


MODELS = (*BIN_MODELS, FlatBalance)


def cleanup(store):
    for model in MODELS:
        store.delete_tree(Path(model.__global__))
        for namespace in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
            store.delete_tree(Path(namespace, (model.__global__[1:].encode(),)))


def setup(store, layout, bins):
    cleanup(store)
    db = Database(store, Registry(*MODELS))
    db.insert(Product(id="P", sku="SKU", name="Product"))
    db.insert(Lot(id="LOT", product=Reference[Product](key="P")))
    count = 2 if bins == 2 else 50
    locations = {
        f"L{i}": Location(id=f"L{i}", bins={f"B{j}": Bin(id=f"B{j}") for j in range(count)})
        for i in range(1 if bins == 2 else 20)
    }
    if layout == "warehouse":
        db.insert(Warehouse(id="W", name="Benchmark", locations=locations))
        return db, Warehouse, "W", ("locations", "L0", "bins", "B0", "balances")
    db.insert(Warehouse(id="W", name="Benchmark"))
    for location in locations.values():
        for bin_ in location.bins.values():
            create_bin(db, BinAddress("W", location.id, bin_.id))
    key = coordinate_key("W", "L0", "B0")
    if layout == "bin":
        return db, StockBin, key, ("balances",)
    return db, FlatBalance, key, ()


def value(model, key):
    values = dict(
        id="LOT",
        lot=Reference[Lot](key="LOT"),
        product=Reference[Product](key="P"),
        quantity=Decimal(1),
    )
    if model is FlatBalance:
        values["position"] = Reference[StockBin](key=key)
    return (FlatBalance if model is FlatBalance else Balance)(**values)


def python_cycles(db, model, key, at, iterations):
    record = value(model, key)
    samples = []
    for i in range(iterations + 3):
        if i == 3:
            db.store.counts.clear()
        started = time.perf_counter_ns()
        if at:
            db.insert_child(model, key, collection=at, record=record)
            db.delete_child(model, key, at=(*at, "LOT"))
        else:
            db.insert(record)
            db.delete(model, "LOT")
        if i >= 3:
            samples.append((time.perf_counter_ns() - started) / 1000)
    counts = {name: count / iterations for name, count in db.store.counts.items()}
    return dict(samples_us=samples, node_operations_per_cycle=counts, **latencies(samples))


def m_cycles(db, model, key, at, iterations):
    stage = Path("^YHMFTestStorage", (b"boundary",))
    db.store.delete_tree(stage)
    db.store.set(stage.child(b"key"), key.encode())
    for i, part in enumerate(at, 1):
        db.store.set(stage.child(b"path", str(i).encode()), part.encode())
    for field, raw in {"id": "LOT", "lot": "LOT", "product": "P", "quantity": "1"}.items():
        db.store.set(stage.child(b"input", field.encode()), raw.encode())
    if not at:
        db.store.set(stage.child(b"input", b"position"), key.encode())
    with tempfile.TemporaryDirectory(prefix="yhmf-boundary-") as temporary:
        folder = FilePath(temporary)
        (folder / "OwnerAPI.m").write_text(db.m_routine(model, name="OwnerAPI"))
        driver = FilePath(__file__).with_name("m") / "YHMFBOUND.m"
        (folder / driver.name).write_bytes(driver.read_bytes())
        env = dict(os.environ, ydb_routines=str(folder) + " " + os.environ["ydb_routines"])
        result = subprocess.run(
            [
                str(FilePath(env["ydb_dist"]) / "yottadb"),
                "-run",
                "%XCMD",
                f"do run^YHMFBOUND({iterations},{int(bool(at))})",
            ],
            env=env,
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        if result.stderr:
            raise RuntimeError(result.stderr)
    samples = [float(line) for line in result.stdout.splitlines()]
    assert len(samples) == iterations
    return dict(samples_us=samples, **latencies(samples))


def verify(db, model):
    if model is Warehouse:
        assert db.get(Warehouse, "W").stored_on_hand == 0
    elif model is FlatBalance:
        assert db.scan(FlatBalance) == []
    for target in (model, StockBin):
        after = None
        while True:
            page = db.scan(target, limit=100, after=after)
            assert db.check_indexes(target, limit=100, after=after).issues == ()
            if len(page) < 100:
                break
            after = page[-1].id
    assert db.referrers(Reference[Lot](key="LOT"), source=model) == []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=15)
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1" or not 1 <= args.iterations <= 100:
        parser.error("Use the disposable native harness and 1-100 iterations")
    store = CountedStore(NativeStore())
    rows = []
    try:
        for layout in ("warehouse", "bin", "balance"):
            for bins in (2, 1000):
                db, model, key, at = setup(store, layout, bins)
                for method, measure in (("python", python_cycles), ("m", m_cycles)):
                    row = measure(db, model, key, at, args.iterations)
                    verify(db, model)
                    rows.append(dict(layout=layout, bins=bins, method=method, **row))
        print(
            "YHMF_BIN_BOUNDARY_BENCHMARK "
            + json.dumps(
                dict(
                    format_version=1,
                    **environment(),
                    operation="insert then delete one balance, separate transactions",
                    warmup_cycles=3,
                    unrelated_balances_per_bin=0,
                    rows=rows,
                ),
                separators=(",", ":"),
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
