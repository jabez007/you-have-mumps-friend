# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline verification of benchmark equivalence and instrumentation, not timings."""

import pytest

from mccann_hub.yhmf import MemoryStore
from scripts.benchmark_inventory import cleanup, measure, operation
from scripts.benchmark_support import CountedStore


@pytest.mark.parametrize("workflow", ["receive", "move", "adjust"])
@pytest.mark.parametrize("method", ["partial", "replace"])
def test_inventory_benchmark_workflows_verify_balances_ledger_indexes_and_references(
    workflow, method
):
    store = CountedStore(MemoryStore())
    result = measure(store, 3, method, workflow, 2)
    assert result["iterations"] == 2
    assert result["warehouse_schema_version"] == 3
    assert len(result["samples_us"]) == 2
    assert result["operations_per_iteration"]["atomic"] == 1
    cleanup(store)


def test_partial_inventory_node_work_is_independent_of_unrelated_balances():
    store = CountedStore(MemoryStore())
    small = measure(store, 2, "partial", "move", 2)
    larger = measure(store, 8, "partial", "move", 2)
    whole = measure(store, 8, "replace", "move", 2)
    assert small["operations_per_iteration"] == larger["operations_per_iteration"]
    assert whole["operations_per_iteration"]["get"] > larger["operations_per_iteration"]["get"]
    # save now applies a physical node difference: the same logical workflow can
    # write the same nodes while still paying for full aggregate reads/auditing.
    assert whole["operations_per_iteration"]["set"] == larger["operations_per_iteration"]["set"]
    assert (
        whole["operations_per_iteration"]["children"]
        > larger["operations_per_iteration"]["children"]
    )


def test_direct_quantity_lower_bound_is_explicitly_not_a_ledger_workflow():
    result = measure(CountedStore(MemoryStore()), 2, "direct_native", "quantity_node_only", 2)
    assert result["warehouse_schema_version"] == 1
    assert result["operations_per_iteration"] == {"atomic": 1, "attempts": 1, "get": 1, "set": 1}
    with pytest.raises(ValueError, match="lower bound"):
        operation(None, "direct_native", "receive")
