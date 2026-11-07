# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify benchmark phases and invariants without inferring native timing."""

import pytest

from mccann_hub.yhmf import MemoryStore
from scripts.benchmark_maintenance import cleanup, measure


@pytest.mark.parametrize("warehouses,batch", [(1, 1), (2, 1), (2, 2)])
def test_inventory_maintenance_workload_refuses_then_completes_the_second_job(warehouses, batch):
    store = MemoryStore()
    row = measure(store, warehouses, lots=2, batch_size=batch)
    assert row["completed"] and row["second_job_completed_after_release"]
    assert "maintenance gate" in row["refusal"]
    assert row["advance_calls_by_phase"]["backfill"] >= 1
    cleanup(store)
