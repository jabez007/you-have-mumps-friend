# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline verification of equivalent benchmark cleanup, never native timing claims."""

import pytest

from mccann_hub.yhmf import MemoryStore
from scripts.benchmark_bin_boundary import cleanup, python_cycles, setup, verify
from scripts.benchmark_support import CountedStore


@pytest.mark.parametrize("layout", ["warehouse", "bin", "balance"])
def test_boundary_benchmark_balances_indexes_and_references_are_restored(layout):
    store = CountedStore(MemoryStore())
    db, model, key, at = setup(store, layout, 2)
    result = python_cycles(db, model, key, at, 2)
    verify(db, model)
    assert len(result["samples_us"]) == 2
    assert result["node_operations_per_cycle"]["atomic"] == 2
    cleanup(store)
