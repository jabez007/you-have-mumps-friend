# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure explicit maintenance refusal and remaining job time on inventory data."""

import json
import os
import time
from collections import Counter
from decimal import Decimal

from examples.inventory import Lot, Product, Warehouse
from examples.inventory.migrations import WAREHOUSE_TOTALS_V3, WarehouseV2
from mccann_hub.yhmf import Database, Field, Model, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import MaintenanceActive
from scripts.benchmark_support import CountedStore, environment


class IndependentControl(Model):
    """A disjoint job target: no references to or from the inventory graph."""

    __global__ = "^YHMFMaintControl"
    id: str = Field(primary_key=True)
    name: str = Field(index=True)


def cleanup(store):
    for model in (Product, Lot, Warehouse, IndependentControl):
        store.delete_tree(Path(model.__global__))
        for name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
            store.delete_tree(Path(name, (model.__global__[1:].encode(),)))


def seed(store, warehouses, lots):
    db = Database(store, Registry(Product, Lot, WarehouseV2, IndependentControl))
    db.insert(IndependentControl(id="C", name="Before maintenance"))
    db.insert(Product(id="P", sku="P", name="Before maintenance"))
    for index in range(lots):
        db.insert(Lot(id=str(index), product=Reference[Product](key="P")))
    for index in range(warehouses):
        db.insert(
            WarehouseV2.model_validate(
                {
                    "id": str(index),
                    "name": "Warehouse",
                    "locations": {
                        location: {
                            "id": location,
                            "bins": {
                                "B1": {
                                    "id": "B1",
                                    "balances": {
                                        str(lot): {
                                            "id": str(lot),
                                            "lot": {"key": str(lot)},
                                            "product": {"key": "P"},
                                            "quantity": Decimal(1),
                                        }
                                        for lot in range(lots)
                                    },
                                }
                            },
                        }
                        for location in ("A", "B")
                    },
                }
            )
        )


def finish(db, status, *, batch_size):
    phases = Counter()
    for _ in range(2000):
        if status.phase == "complete":
            return status, dict(phases)
        phases[status.phase] += 1
        # New handles model application/process resume between committed batches.
        db = Database(db.store, db.registry)
        status = db.advance_migration(
            status.migration_id,
            WarehouseV2,
            Warehouse,
            transform=WAREHOUSE_TOTALS_V3,
            batch_size=batch_size,
        )
    raise RuntimeError("Maintenance benchmark exceeded its progress bound")


def measure(store, warehouses, *, lots=10, batch_size=5):
    cleanup(store)
    seed(store, warehouses, lots)
    counted = CountedStore(store)
    db = Database(counted, Registry(Product, Lot, Warehouse, IndependentControl))
    rebuild = db.plan_index_rebuild(IndependentControl)
    assert store.get(Path("^YHMFRef", (b"YHMFMaintControl", b"s:C"))) is None
    plan = db.plan_migration(Warehouse, transform=WAREHOUSE_TOTALS_V3)
    status = db.begin_migration(WarehouseV2, Warehouse, plan=plan, transform=WAREHOUSE_TOTALS_V3)
    counted.counts.clear()
    started = time.perf_counter_ns()
    try:
        db.begin_index_rebuild(IndependentControl, plan=rebuild)
    except MaintenanceActive as error:
        refusal = str(error)
    else:
        raise RuntimeError("A second maintenance job unexpectedly began")
    refused = time.perf_counter_ns()
    assert db.migration_status(status.migration_id) == status
    crud_started = time.perf_counter_ns()
    db.patch(IndependentControl, "C", changes={"name": "Changed during warehouse maintenance"})
    patched = time.perf_counter_ns()
    status, phases = finish(db, status, batch_size=batch_size)
    finished = time.perf_counter_ns()
    assert status.phase == "complete" and status.verified == warehouses
    for index in range(warehouses):
        root = db.get(Warehouse, str(index))
        assert root.stored_on_hand == 2 * lots
        assert all(location.stored_on_hand == lots for location in root.locations.values())
    for model in (Product, Lot, Warehouse, IndependentControl):
        assert db.check_indexes(model, limit=1000).issues == ()
    assert db.get(IndependentControl, "C").name == "Changed during warehouse maintenance"
    # The same previously refused plan is valid once ownership is released.
    second = db.begin_index_rebuild(IndependentControl, plan=rebuild)
    for _ in range(100):
        if second.phase == "complete":
            break
        second = db.advance_index_rebuild(
            second.migration_id, IndependentControl, batch_size=batch_size
        )
    assert second.phase == "complete"
    assert db.check_indexes(IndependentControl).issues == ()
    return {
        "warehouses": warehouses,
        "locations_per_warehouse": 2,
        "lots_per_location": lots,
        "batch_size": batch_size,
        "second_global": IndependentControl.__global__,
        "second_model_has_inventory_references": False,
        "refusal": refusal,
        "refusal_ms": (refused - started) / 1_000_000,
        "unrelated_crud_ms": (patched - crud_started) / 1_000_000,
        "remaining_job_ms": (finished - patched) / 1_000_000,
        "total_after_refusal_ms": (finished - refused) / 1_000_000,
        "advance_calls_by_phase": phases,
        "completed": True,
        "second_job_completed_after_release": True,
        "counts_including_verification_and_second_job": dict(counted.counts),
    }


def main():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        raise SystemExit("Use scripts/test-native.sh's disposable database")
    store = NativeStore()
    try:
        rows = [measure(store, size) for size in (1, 10, 50)]
        print(
            "YHMF_MAINTENANCE_BENCHMARK="
            + json.dumps(
                {
                    "format_version": 1,
                    **environment(),
                    "results": rows,
                    "methodology": (
                        "Inventory warehouse-v2-to-v3 transform. Refuse an independent control "
                        "index rebuild; verify the active checkpoint is unchanged. Time unrelated "
                        "CRUD and remaining batches, then complete the refused rebuild. "
                        "Fresh handles resume batches; no operator pauses or queue are measured. "
                        "Synthetic scheduling evidence, not a production waiting-time prediction."
                    ),
                },
                sort_keys=True,
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
