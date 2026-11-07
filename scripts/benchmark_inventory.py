# SPDX-License-Identifier: GPL-3.0-or-later
"""Native inventory workflows; run as a module inside the disposable test database."""

import argparse
import json
import os
import time
from decimal import Decimal, localcontext

from examples.inventory import (
    MODELS,
    Balance,
    Bin,
    BinAddress,
    Inventory,
    Location,
    Lot,
    Movement,
    Product,
    Warehouse,
)
from examples.inventory.migrations import LegacyWarehouse
from mccann_hub.yhmf import Database, NativeStore, Path, Reference, Registry
from scripts.benchmark_support import CountedStore, environment, latencies

WARMUP = 5
DOCK, STORAGE = BinAddress("MAIN", "DOCK", "B1"), BinAddress("MAIN", "STORAGE", "B1")
STAMP = "2026-11-06T12:00:00+00:00"


class WholeAggregateInventory(Inventory):
    """Equivalent workflow; group both sides of a move into one save per root."""

    def _apply_balances(self, tx, changes):
        roots = {}
        for change in changes:
            address, value = change.address, change.value
            if address.warehouse not in roots:
                roots[address.warehouse] = tx.get(Warehouse, address.warehouse)
            root = roots[address.warehouse]
            root.locations[address.location].bins[address.bin].balances[value.id] = value
        for root in roots.values():
            tx.save(root)


def cleanup(store):
    for model in MODELS:
        store.delete_tree(Path(model.__global__))
        for name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
            store.delete_tree(Path(name, (model.__global__[1:].encode(),)))


def setup(store, size, *, quantity_only=False):
    model = LegacyWarehouse if quantity_only else Warehouse
    models = (Product, Lot, LegacyWarehouse) if quantity_only else MODELS
    db = Database(store, Registry(*models))
    db.insert(Product(id="WIDGET", sku="WIDGET", name="Widget"))
    for index in range(size):
        db.insert(Lot(id=f"LOT-{index:04}", product=Reference[Product](key="WIDGET")))
    locations = {}
    for location in ("DOCK", "STORAGE"):
        balances = {}
        for index in range(size):
            lot = f"LOT-{index:04}"
            quantity = Decimal(1000) if location == "DOCK" and index == 0 else Decimal(0)
            balances[lot] = Balance(
                id=lot,
                lot=Reference[Lot](key=lot),
                product=Reference[Product](key="WIDGET"),
                quantity=quantity,
            )
        locations[location] = Location(
            id=location, bins={"B1": Bin(id="B1", balances=balances)}
        ).model_dump(exclude={"stored_on_hand"} if quantity_only else set())
    db.insert(
        model.model_validate(
            {
                "id": "MAIN",
                "name": "Main",
                "locations": locations,
            }
        )
    )
    return db


def operation(db, method, workflow):
    if method not in {"partial", "replace", "direct_native"}:
        raise ValueError("Unknown inventory benchmark method")
    if workflow not in {"receive", "move", "adjust", "quantity_node_only"}:
        raise ValueError("Unknown inventory benchmark workflow")
    if (method == "direct_native") != (workflow == "quantity_node_only"):
        raise ValueError("Direct native assignment only measures the quantity-node lower bound")
    service = WholeAggregateInventory(db) if method == "replace" else Inventory(db)
    counter = 0
    node = Path(
        Warehouse.__global__,
        (
            b"s:MAIN",
            b"locations",
            b"s:DOCK",
            b"bins",
            b"s:B1",
            b"balances",
            b"s:LOT-0000",
            b"quantity",
        ),
    )

    def direct():
        with localcontext() as context:
            context.prec = 40
            current = db.store.get(node)
            assert current.startswith(b"d:")
            value = Decimal(current[2:].decode()) + Decimal(1)
            db.store.set(node, b"d:" + str(value).encode())

    def mutate():
        nonlocal counter
        counter += 1  # Stable ID outside the retryable callback.
        movement = f"{counter:06}"
        if method == "direct_native":
            db.store.atomic(direct)
        elif workflow == "receive":
            service.receive(movement, "LOT-0000", DOCK, Decimal(1), occurred_at=STAMP)
        elif workflow == "move":
            service.move(movement, "LOT-0000", DOCK, STORAGE, Decimal(1), occurred_at=STAMP)
        else:
            service.adjust(
                movement,
                "LOT-0000",
                DOCK,
                Decimal(1),
                reason="Benchmark cycle count",
                occurred_at=STAMP,
            )

    return mutate


def verify(db, size, workflow, completed):
    quantity_only = workflow == "quantity_node_only"
    model = LegacyWarehouse if quantity_only else Warehouse
    root = db.get(model, "MAIN")
    for location in root.locations.values():
        balances = location.bins["B1"].balances
        assert len(balances) == size
        assert all(balance.quantity == 0 for key, balance in balances.items() if key != "LOT-0000")
    dock = root.locations["DOCK"].bins["B1"].balances["LOT-0000"].quantity
    storage = root.locations["STORAGE"].bins["B1"].balances["LOT-0000"].quantity
    assert dock == (1000 - completed if workflow == "move" else 1000 + completed)
    assert storage == (completed if workflow == "move" else 0)
    movements = 0 if quantity_only else completed
    if quantity_only:
        assert db.store.children(Path(Movement.__global__), limit=1) == ()
    else:
        assert len(db.scan(Movement, limit=1000)) == movements
        assert root.stored_on_hand == root.total_on_hand
    for indexed in (Product, Lot, model) if quantity_only else MODELS:
        assert db.check_indexes(indexed, limit=1000).issues == ()
    assert inbound(db, Product, "WIDGET") == 3 * size + movements
    assert inbound(db, Lot, "LOT-0000") == 2 + movements
    assert inbound(db, Warehouse, "MAIN") == movements * (2 if workflow == "move" else 1)


def inbound(db, model, key):
    return int(
        db.store.get(Path("^YHMFRef", (model.__global__[1:].encode(), ("s:" + key).encode())))
        or b"0"
    )


def measure(store, size, method, workflow, iterations):
    cleanup(store)
    db = setup(store, size, quantity_only=method == "direct_native")
    mutate = operation(db, method, workflow)
    for _ in range(WARMUP):
        mutate()
    store.counts.clear()
    elapsed = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        mutate()
        elapsed.append((time.perf_counter_ns() - started) / 1000)
    counts = {key: value / iterations for key, value in sorted(store.counts.items())}
    verify(db, size, workflow, iterations + WARMUP)
    return {
        "lots": size,
        "balances_per_bin": size,
        "workflow": workflow,
        "method": method,
        "warehouse_schema_version": 1
        if method == "direct_native"
        else Warehouse.__schema_version__,
        "iterations": iterations,
        "warmup": WARMUP,
        **latencies(elapsed),
        "p99_us": round(sorted(elapsed)[min(len(elapsed) - 1, int(len(elapsed) * 0.99))], 2),
        "samples_us": [round(value, 2) for value in elapsed],
        "operations_per_iteration": counts,
    }


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 50, 250])
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Run only in scripts/test-native.sh's disposable database")
    if not 10 <= args.iterations <= 200 or not 1 <= len(args.sizes) <= 6:
        parser.error("Use 10 to 200 iterations and at most six sizes")
    if any(not 1 <= size <= 500 for size in args.sizes):
        parser.error("Use sizes from 1 to 500")
    return args


def main():
    args = arguments()
    store = CountedStore(NativeStore())
    try:
        results = []
        for size in args.sizes:
            results.append(
                measure(store, size, "direct_native", "quantity_node_only", args.iterations)
            )
            for workflow in ("receive", "move", "adjust"):
                for method in ("partial", "replace"):
                    results.append(measure(store, size, method, workflow, args.iterations))
        print(
            "YHMF_INVENTORY_BENCHMARK="
            + json.dumps(
                {
                    "format_version": 1,
                    **environment(),
                    "fixture": {"warehouses": 1, "locations": 2, "bins": 2},
                    "methodology": (
                        "Warm, single-process, counted native operations. Existing balances; "
                        "one ledger append per workflow. Direct assignment excludes all ledger "
                        "and integrity work, using the frozen v1 warehouse without stored sums. "
                        "Current workflows maintain v3 warehouse and location sums/indexes. "
                        "Disposable region configured by the harness; "
                        "the journaling field records its mode."
                    ),
                    "results": results,
                },
                sort_keys=True,
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
