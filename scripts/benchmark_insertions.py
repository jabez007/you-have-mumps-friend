# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare insertion work as a target or unrelated flat collection grows."""

import time
from collections import Counter
from decimal import Decimal

from benchmark_support import Bin, Lot, Warehouse, latencies, make_warehouse

from mccann_hub.yhmf import Path
from mccann_hub.yhmf.codecs import encode_scalar


def operation(db, destination, method):
    record = Lot(id="new", quantity=Decimal("2.5"))
    collection = ("bins", destination, "lots")
    path = Path(Warehouse.__global__, (b"s:W", b"bins", f"s:{destination}".encode(), b"lots"))

    def direct():
        count = int(db.store.get(path)[2:])
        child = path.child(b"s:new")
        db.store.set(child, b"o:1")
        db.store.set(child.child(b"id"), encode_scalar(record.id))
        db.store.set(child.child(b"quantity"), encode_scalar(record.quantity))
        db.store.set(path, f"c:{count + 1}".encode())

    def replace(tx):
        root = tx.get(Warehouse, "W")
        root.bins[destination].lots[record.id] = record
        tx.save(root)

    if method == "direct":
        return lambda: db.store.atomic(direct)
    if method == "insert_child":
        return lambda: db.insert_child(Warehouse, "W", collection=collection, record=record)
    if method == "replace":
        return lambda: db.atomic(replace)
    raise ValueError("Unknown insertion benchmark method")


def measure(db, size, method, iterations, pattern):
    if not 1 <= size <= 999 or iterations < 1:
        raise ValueError("Insertion benchmark requires size 1..999 and positive iterations")
    if pattern not in {"target_collection", "unrelated_collection"}:
        raise ValueError("Unknown insertion pattern")
    before = make_warehouse("unindexed", size)
    destination = "A" if pattern == "target_collection" else "B"
    if destination == "B":
        before.bins["B"] = Bin(id="B")
    db.save(before)
    mutate = operation(db, destination, method)
    elapsed, counts = [], Counter()
    for iteration in range(iterations + 5):
        db.store.counts.clear()
        start = time.perf_counter_ns()
        mutate()
        duration = (time.perf_counter_ns() - start) / 1000
        if iteration >= 5:
            elapsed.append(duration)
            counts.update(db.store.counts)
        if iteration == iterations + 4:
            expected = before.model_copy(deep=True)
            expected.bins[destination].lots["new"] = Lot(id="new", quantity=Decimal("2.5"))
            assert db.get(Warehouse, "W") == expected
            assert db.check_indexes(Warehouse).issues == ()
        # Reset outside the timed and counted interval. Every sample starts with
        # the same aggregate, with room for one child under the 1,000-child cap.
        db.delete_child(Warehouse, "W", at=("bins", destination, "lots", "new"))
    assert db.get(Warehouse, "W") == before
    return {
        "pattern": pattern,
        "existing_lots": size,
        "method": method,
        "iterations": iterations,
        **latencies(elapsed),
        "operations_per_iteration": {
            name: count / iterations for name, count in sorted(counts.items())
        },
    }
