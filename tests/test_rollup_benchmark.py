# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline evidence for workload equivalence, never native timing claims."""

import pytest

from mccann_hub.yhmf import MemoryStore
from scripts import benchmark_rollups as benchmark
from scripts.benchmark_support import CountedStore


@pytest.mark.parametrize("mode", benchmark.MODELS)
@pytest.mark.parametrize("pattern", benchmark.PATTERNS)
def test_rollup_benchmark_layouts_verify_identical_final_quantities(mode, pattern, monkeypatch):
    store = MemoryStore()

    def serial_processes(actual_mode, actual_pattern, workers, iterations):
        model = benchmark.MODELS[actual_mode]
        counted = CountedStore(store)
        from mccann_hub.yhmf import Database, Registry

        db = Database(counted, Registry(model))
        for index in range(workers):
            for _ in range(iterations):
                benchmark.increment(db, model, benchmark.address(actual_pattern, index))
        return [{"counts": dict(counted.counts), "samples_us": [1] * workers * iterations}], 1.0

    monkeypatch.setattr(benchmark, "processes", serial_processes)
    row = benchmark.measure(store, mode, pattern, 2, 3, 2)
    assert row["commits"] == row["callback_attempts"] == 4
    assert row["samples_us"] == [1] * 4
    assert row["callback_retries"] == 0
    assert row["totals"] == mode
    assert row["pattern"] == pattern
    benchmark.cleanup(store)
