# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare native contention with no totals, root totals, and owned totals."""

import argparse
import json
import multiprocessing
import os
import time
from collections import Counter
from decimal import Decimal

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
from scripts.benchmark_support import CountedStore, environment, latencies


class Quantity(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Decimal(0)


class Location(Model):
    id: str = Field(primary_key=True)
    items: Children[Quantity] = ChildField(key="id")


class StoredLocation(Location):
    total: Decimal = Field(sum_of="items.quantity", index=True)


class NoTotals(Model):
    __global__ = "^YHMFRollupNone"
    id: str = Field(primary_key=True)
    locations: Children[Location] = ChildField(key="id")


class RootTotal(NoTotals):
    __global__ = "^YHMFRollupRoot"
    total: Decimal = Field(sum_of="locations.items.quantity", index=True)


class OwnedTotals(RootTotal):
    __global__ = "^YHMFRollupOwned"
    locations: Children[StoredLocation] = ChildField(key="id")


MODELS = {"none": NoTotals, "root": RootTotal, "root_and_owned": OwnedTotals}
PATTERNS = ("same_location", "different_locations")


def address(pattern, index):
    return (
        "locations",
        "B" if pattern == "different_locations" and index % 2 else "A",
        "items",
        str(index),
    )


def cleanup(store):
    for model in MODELS.values():
        store.delete_tree(Path(model.__global__))
        for name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique"):
            store.delete_tree(Path(name, (model.__global__[1:].encode(),)))


def seed(store, mode, size):
    model = MODELS[mode]
    db = Database(store, Registry(model))
    db.insert(
        model.model_validate(
            {
                "id": "W",
                "locations": {
                    location: {
                        "id": location,
                        "items": {
                            str(index): {"id": str(index), "quantity": Decimal(0)}
                            for index in range(size)
                        },
                    }
                    for location in ("A", "B")
                },
            }
        )
    )
    return db


def increment(db, model, at):
    def change(tx):
        value = tx.get(model, "W", at=at).quantity
        tx.patch(model, "W", at=at, changes={"quantity": value + Decimal(1)})

    db.atomic(change)


def worker(mode, pattern, index, iterations, barrier, results):
    try:
        model, store = MODELS[mode], CountedStore(NativeStore())
        db = Database(store, Registry(model))
        at = address(pattern, index)
        db.get(model, "W", at=at)  # Warm connection before the coordinated burst.
        store.counts.clear()
        barrier.wait(timeout=30)
        samples = []
        for _ in range(iterations):
            started = time.perf_counter_ns()
            increment(db, model, at)
            samples.append((time.perf_counter_ns() - started) / 1000)
        results.put({"samples_us": samples, "counts": dict(store.counts)})
    except BaseException as error:
        results.put({"error": repr(error)})
        barrier.abort()
        raise


def processes(mode, pattern, workers, iterations):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(workers + 1), context.Queue()
    children = [
        context.Process(target=worker, args=(mode, pattern, index, iterations, barrier, results))
        for index in range(workers)
    ]
    try:
        for child in children:
            child.start()
        barrier.wait(timeout=30)
        started = time.perf_counter()
        values = [results.get(timeout=60) for _ in children]
        elapsed = time.perf_counter() - started
        for child in children:
            child.join(timeout=10)
            if child.exitcode != 0:
                raise RuntimeError(f"Owned-total benchmark worker failed: {values}")
        if any("error" in value for value in values):
            raise RuntimeError(f"Owned-total benchmark failed: {values}")
        return values, elapsed
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
            if child.pid is not None:
                child.join(timeout=10)
        results.close()
        results.join_thread()


def verify(db, mode, pattern, workers, iterations):
    model = MODELS[mode]
    root = db.get(model, "W")
    expected = Counter(address(pattern, index) for index in range(workers))
    for name, location in root.locations.items():
        for key, item in location.items.items():
            assert item.quantity == expected[("locations", name, "items", key)] * iterations
        if mode == "root_and_owned":
            assert location.total == sum((v.quantity for v in location.items.values()), Decimal(0))
    if mode != "none":
        assert root.total == workers * iterations
    assert db.check_indexes(model).issues == ()


def measure(store, mode, pattern, workers, size, iterations):
    cleanup(store)
    db = seed(store, mode, size)
    values, elapsed = processes(mode, pattern, workers, iterations)
    verify(db, mode, pattern, workers, iterations)
    counts, samples = Counter(), []
    for value in values:
        counts.update(value["counts"])
        samples.extend(value["samples_us"])
    commits = workers * iterations
    assert counts["atomic"] == len(samples) == commits
    assert counts["attempts"] >= commits
    return {
        "totals": mode,
        "pattern": pattern,
        "workers": workers,
        "items_per_location": size,
        "iterations_per_worker": iterations,
        "commits": commits,
        "callback_attempts": counts["attempts"],
        "callback_retries": counts["attempts"] - commits,
        "elapsed_s": elapsed,
        "commits_per_second": commits / elapsed,
        "operations_per_commit": {key: value / commits for key, value in sorted(counts.items())},
        "samples_us": samples,
        **latencies(samples),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--size", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Use scripts/test-native.sh's disposable database")
    if not (
        2 <= args.workers <= 4
        and 10 <= args.iterations <= 100
        and args.workers <= args.size <= 100
        and 1 <= args.repeats <= 5
    ):
        parser.error("Use 2-4 workers, 10-100 iterations, workers-100 items, and 1-5 repeats")
    store = NativeStore()
    try:
        rows = [
            {
                "repeat": repeat,
                **measure(store, mode, pattern, args.workers, args.size, args.iterations),
            }
            for repeat in range(args.repeats)
            for pattern in PATTERNS
            for mode in MODELS
        ]
        print(
            "YHMF_ROLLUP_CONTENTION_BENCHMARK="
            + json.dumps(
                {
                    "format_version": 1,
                    **environment(),
                    "results": rows,
                    "methodology": (
                        "Coordinated process bursts; independent existing children; exact "
                        "increments with retry-local reads. Setup and warm connections excluded; "
                        "throughput includes collecting worker results. All totals indexed. "
                        "Compare no sums, root sums, root plus owned sums. Shared native and index "
                        "blocks can conflict without shared logical nodes. Synthetic contention; "
                        "not a production conflict-rate prediction."
                    ),
                },
                sort_keys=True,
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
