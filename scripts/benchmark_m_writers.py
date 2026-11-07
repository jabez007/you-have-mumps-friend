# SPDX-License-Identifier: GPL-3.0-or-later
"""Native M memoization controls. Run only in the disposable native harness."""

import argparse
import json
import os
import selectors
import subprocess
import tempfile
import time
from decimal import Decimal
from pathlib import Path as FilePath

from examples.inventory import MODELS, Balance, Bin, Location, Lot, Product, Warehouse
from mccann_hub.yhmf import Database, NativeStore, Reference, Registry
from scripts.benchmark_inventory import cleanup
from scripts.benchmark_support import CountedStore, environment, latencies

OPERATIONS = ("insert", "patch", "delete")
HASH_ENTRY = "\nsha256(value)\n"
ATTEMPT_ENTRY = "\nhattempt(operation,rootkey,parts,node,wire,rootcfg)\n"
CACHE_CALL = "$$locationhash(location)"


def seed(store, workers, shared, lots):
    cleanup(store)
    db = Database(store, Registry(*MODELS))
    for index in range(1 if shared else workers):
        db.insert(Product(id=f"P{index}", sku=f"SKU{index}", name="Benchmark product"))
    for index in range(workers):
        product = f"P{0 if shared else index}"
        for item in range(lots + 1):
            db.insert(Lot(id=f"L{index}-{item}", product=Reference[Product](key=product)))
        db.insert(
            Warehouse(
                id=f"W{index}",
                name="Benchmark warehouse",
                locations={
                    name: Location(id=name, bins={"B1": Bin(id="B1")})
                    for name in ("DOCK", "STORAGE")
                },
            )
        )
    return db


def exported(db, *, uncached=False):
    source = db.m_routine(Warehouse, name="YHMFBAPI")
    assert source.count(HASH_ENTRY) == source.count(ATTEMPT_ENTRY) == source.count(CACHE_CALL) == 1
    source = source.replace(HASH_ENTRY, HASH_ENTRY + " set %YHMFHashCalls=$get(%YHMFHashCalls)+1\n")
    source = source.replace(
        ATTEMPT_ENTRY, ATTEMPT_ENTRY + " set %YHMFAttempts=$get(%YHMFAttempts)+1\n"
    )
    return source.replace(CACHE_CALL, "$$sha256(location)") if uncached else source


def verify(db, workers, shared, lots):
    for index in range(workers):
        root = db.get(Warehouse, f"W{index}")
        assert root.stored_on_hand == root.total_on_hand == 0
        assert all(location.stored_on_hand == 0 for location in root.locations.values())
        assert root.locations["DOCK"].bins["B1"].balances == {}
        for item in range(lots + 1):
            assert db.referrers(Reference[Lot](key=f"L{index}-{item}"), source=Warehouse) == []
    for index in range(1 if shared else workers):
        product = Reference[Product](key=f"P{index}")
        assert db.referrers(product, source=Warehouse) == []
        assert len(db.referrers(product, source=Lot)) == (workers if shared else 1) * (lots + 1)
    assert all(db.check_indexes(model).issues == () for model in (Product, Lot, Warehouse))


def ready(worker):
    with selectors.DefaultSelector() as selector:
        selector.register(worker.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=30):
            raise RuntimeError("M benchmark worker did not reach its barrier")
    message = worker.stdout.readline().strip()
    if message != "ready":
        raise RuntimeError(f"M benchmark failed before its coordinated start: {message}")


def collect(children):
    started = time.perf_counter()
    for child in children:
        child.stdin.write("go\n")
        child.stdin.flush()
    values = []
    for child in children:
        output, error = child.communicate(timeout=90)
        if child.returncode or error:
            raise RuntimeError(f"M benchmark worker failed: {error} {output}")
        values.append(output.splitlines())
    return values, time.perf_counter() - started


def native_workers(folder, workers, shared, iterations, workload):
    env = dict(os.environ)
    env["ydb_routines"] = str(folder) + " " + env["ydb_routines"]
    binary = str(FilePath(env["ydb_dist"]) / "yottadb")
    subprocess.run(
        [binary, "-run", "%XCMD", f'zcompile "{folder / "YHMFBAPI.m"}"'],
        env=env,
        cwd=folder,
        capture_output=True,
        text=True,
        check=True,
    )
    children = []
    try:
        for index in range(workers):
            product = f"P{0 if shared else index}"
            command = (
                f'do run^YHMFBENCH("W{index}","L{index}-","{product}",{iterations},"{workload}")'
            )
            child = subprocess.Popen(
                [binary, "-run", "%XCMD", command],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            children.append(child)
            ready(child)
        return collect(children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)


def summarize(values, *, method, workload, workers, shared, iterations, elapsed):
    samples = {operation: [] for operation in OPERATIONS}
    hashes = {operation: 0 for operation in OPERATIONS}
    attempts = {operation: 0 for operation in OPERATIONS}
    cache_sizes = []
    worker_elapsed = []
    for worker in values:
        for line in worker:
            parts = line.split("|")
            if parts[0] == "cache":
                cache_sizes.append(int(parts[1]))
            elif parts[0] == "elapsed":
                worker_elapsed.append(int(parts[1]))
            else:
                assert parts[0] == "sample" and len(parts) == 5
                _, operation, latency, calls, retries = parts
                samples[operation].append(float(latency))
                hashes[operation] += int(calls)
                attempts[operation] += int(retries)
    assert len(cache_sizes) == workers and max(cache_sizes) <= 128
    assert len(worker_elapsed) == workers
    rows = []
    for operation in OPERATIONS:
        values = samples[operation]
        assert len(values) == iterations * workers and attempts[operation] >= len(values)
        rows.append(
            {
                "method": method,
                "workload": workload,
                "operation": operation,
                "workers": workers,
                "product_refs": "shared" if shared else "separate",
                "iterations_per_worker": iterations,
                "samples_us": values,
                **latencies(values),
                "sha256_calls": hashes[operation],
                "tp_attempts": attempts[operation],
                "tp_retries": attempts[operation] - len(values),
                "worker_cache_entries": cache_sizes,
                "worker_elapsed_us": worker_elapsed,
                "burst_seconds": elapsed,
                "burst_operations_per_second": 3 * iterations * workers / elapsed,
            }
        )
    return rows


def native_case(store, *, method, workload, workers, shared, iterations):
    lots = iterations if workload == "churn" else 0
    db = seed(store, workers, shared, lots)
    with tempfile.TemporaryDirectory(prefix="yhmf-m-bench-") as temporary:
        folder = FilePath(temporary)
        (folder / "YHMFBAPI.m").write_text(exported(db, uncached=method == "m_uncached"))
        driver = FilePath(__file__).with_name("m") / "YHMFBENCH.m"
        (folder / driver.name).write_bytes(driver.read_bytes())
        values, elapsed = native_workers(folder, workers, shared, iterations, workload)
    verify(db, workers, shared, lots)
    return summarize(
        values,
        method=method,
        workload=workload,
        workers=workers,
        shared=shared,
        iterations=iterations,
        elapsed=elapsed,
    )


def python_case(store, iterations):
    counted = CountedStore(store)
    db = seed(counted, 1, False, 0)
    at = ("locations", "DOCK", "bins", "B1", "balances")
    record = Balance(
        id="L0-0",
        lot=Reference[Lot](key="L0-0"),
        product=Reference[Product](key="P0"),
        quantity=Decimal(1),
    )
    operations = (
        lambda: db.insert_child(Warehouse, "W0", collection=at, record=record),
        lambda: db.patch(Warehouse, "W0", at=(*at, "L0-0"), changes={"quantity": Decimal(2)}),
        lambda: db.delete_child(Warehouse, "W0", at=(*at, "L0-0")),
    )
    samples = {name: [] for name in OPERATIONS}
    for index in range(iterations + 3):
        if index == 3:
            counted.counts.clear()
        for name, operation in zip(OPERATIONS, operations, strict=True):
            started = time.perf_counter_ns()
            operation()
            if index >= 3:
                samples[name].append((time.perf_counter_ns() - started) / 1000)
    counts = dict(counted.counts)
    verify(db, 1, False, 0)
    return [
        {
            "method": "python",
            "workload": "hot",
            "operation": name,
            "workers": 1,
            "product_refs": "separate",
            "node_calls_total": counts,
            "samples_us": values,
            **latencies(values),
        }
        for name, values in samples.items()
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1" or not 1 <= args.iterations <= 100:
        parser.error("Use the disposable native harness and 1-100 iterations")
    store = NativeStore()
    rows = []
    try:
        for method in ("m_uncached", "m_cached"):
            for workload, workers, shared in (
                ("hot", 1, False),
                ("hot", 4, False),
                ("hot", 4, True),
                ("cold", 1, False),
                ("churn", 1, False),
            ):
                rows.extend(
                    native_case(
                        store,
                        method=method,
                        workload=workload,
                        workers=workers,
                        shared=shared,
                        iterations=70 if workload == "churn" else args.iterations,
                    )
                )
        rows.extend(python_case(store, args.iterations))
        print(
            "YHMF_M_WRITER_BENCHMARK "
            + json.dumps(
                {
                    "format_version": 1,
                    **environment(),
                    "warehouse_schema_version": Warehouse.__schema_version__,
                    "warmup_cycles": 3,
                    "cold_cache_clear": "before each operation, outside timing",
                    "control": "same generated source, bypassing only locationhash memoization",
                    "rows": rows,
                },
                separators=(",", ":"),
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
