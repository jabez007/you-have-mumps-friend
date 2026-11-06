# SPDX-License-Identifier: GPL-3.0-or-later
"""Synchronized native process bursts, with correctness checks after measurement."""

import json
import selectors
import subprocess
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path as FilePath

from benchmark_support import (
    ROOT_MODELS,
    CountedStore,
    Product,
    latencies,
    make_warehouse,
    model_for,
    verify_references,
)

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from mccann_hub.yhmf.codecs import decode_key

PATTERNS = ("separate_roots", "separate_children", "same_child")


def location(pattern, index):
    return (
        f"W{index}" if pattern == "separate_roots" else "W",
        str(index) if pattern == "separate_children" else "0",
    )


def increment_operation(db, model, key, lot, method):
    at = ("bins", "A", "lots", lot)

    def increment():
        def change(tx):
            if method == "patch":
                current = tx.get(model, key, at=at)
                tx.patch(model, key, at=at, changes={"quantity": current.quantity + Decimal(1)})
            else:
                root = tx.get(model, key)
                root.bins["A"].lots[lot].quantity += 1
                tx.save(root)

        db.atomic(change)

    return increment


def worker(config):
    store = CountedStore(NativeStore())
    db = Database(store, Registry(Product, *ROOT_MODELS))
    model = model_for(config["workload"])
    key, lot = location(config["pattern"], config["index"])
    # Warm native connection before rendezvous. The mutation reads again in TP.
    db.get(model, key, at=("bins", "A", "lots", lot))
    mutate = increment_operation(db, model, key, lot, config["method"])
    store.counts.clear()
    print("ready", flush=True)
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Benchmark controller did not release worker")
    elapsed = []
    for _ in range(config["iterations"]):
        start = time.perf_counter_ns()
        mutate()
        elapsed.append((time.perf_counter_ns() - start) / 1000)
    print(json.dumps({"samples_us": elapsed, "counts": dict(store.counts)}), flush=True)


def _ready(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(30):
            raise RuntimeError("Native benchmark worker did not reach rendezvous")
    if process.stdout.readline().strip() != "ready":
        _, error = process.communicate(timeout=10)
        raise RuntimeError(f"Native benchmark worker failed before rendezvous: {error}")


def _processes(configs):
    script = FilePath(__file__).with_name("benchmark-mutations.py")
    workers = []
    try:
        for config in configs:
            workers.append(
                subprocess.Popen(
                    [sys.executable, str(script), "--worker", json.dumps(config)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in workers:
            _ready(process)
        started = time.perf_counter()
        for process in workers:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in workers:
            output, error = process.communicate(timeout=90)
            if process.returncode:
                raise RuntimeError(f"Native benchmark worker failed: {error}")
            results.append(json.loads(output))
        return results, time.perf_counter() - started
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def seed(db, workload, pattern, workers, size):
    # Each burst starts from the same state; remove earlier benchmark records
    # through the mapper so reference counts and unique claims stay valid.
    for model in ROOT_MODELS:
        keys = db.store.children(Path(model.__global__), limit=10)
        if len(keys) == 10:
            raise RuntimeError("Unexpected benchmark root count")
        for key in keys:
            db.delete(model, decode_key(key))
    roots = [f"W{i}" for i in range(workers)] if pattern == "separate_roots" else ["W"]
    for key in roots:
        db.insert(make_warehouse(workload, size, key))


def verify(db, workload, pattern, workers, iterations, size):
    model = model_for(workload)
    roots = {location(pattern, i)[0] for i in range(workers)}
    expected = Counter(location(pattern, i) for i in range(workers))
    for key in roots:
        lots = db.get(model, key).bins["A"].lots
        assert len(lots) == size
        for lot, value in lots.items():
            assert value.quantity == expected[(key, lot)] * iterations
    assert db.check_indexes(model, limit=10).issues == ()
    verify_references(db)


def measure(db, workload, method, pattern, workers, size, iterations):
    seed(db, workload, pattern, workers, size)
    configs = [
        {
            "workload": workload,
            "method": method,
            "pattern": pattern,
            "index": index,
            "iterations": iterations,
        }
        for index in range(workers)
    ]
    results, elapsed = _processes(configs)
    verify(db, workload, pattern, workers, iterations, size)
    counts = Counter()
    samples = []
    for result in results:
        counts.update(result["counts"])
        samples.extend(result["samples_us"])
    commits = workers * iterations
    assert len(samples) == counts["atomic"] == commits
    assert counts["attempts"] >= commits
    return {
        "workload": workload,
        "method": method,
        "pattern": pattern,
        "workers": workers,
        "lots_per_root": size,
        "iterations_per_worker": iterations,
        "committed": commits,
        "callback_attempts": counts["attempts"],
        "callback_retries": counts["attempts"] - commits,
        "elapsed_s": round(elapsed, 6),
        "commits_per_second": round(commits / elapsed, 2),
        **latencies(samples),
        "operations_per_commit": {name: value / commits for name, value in sorted(counts.items())},
    }
