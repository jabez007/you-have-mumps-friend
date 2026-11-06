# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline workload checks establish correctness, not native timing evidence."""

import importlib
from decimal import Decimal
from pathlib import Path as FilePath
from types import SimpleNamespace

import pytest

from mccann_hub.yhmf import MemoryStore, Path
from mccann_hub.yhmf.errors import ReferenceViolation, UniqueViolation


@pytest.fixture(scope="module")
def benchmarks():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(FilePath(__file__).parents[1] / "scripts"))
        yield SimpleNamespace(
            support=importlib.import_module("benchmark_support"),
            contention=importlib.import_module("benchmark_contention"),
            profiles=importlib.import_module("benchmark_profiles"),
        )


@pytest.fixture
def db(benchmarks):
    b = benchmarks.support
    store = b.CountedStore(MemoryStore())
    result = b.setup(store)
    yield result
    b.cleanup(store)
    for model in (b.Product, *b.ROOT_MODELS):
        assert store.children(Path(model.__global__), limit=1) == ()
        for name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
            assert store.children(Path(name, (model.__global__[1:].encode(),)), limit=1) == ()


@pytest.mark.parametrize("workload", ["unindexed", "indexed", "unique", "reference"])
@pytest.mark.parametrize("method", ["patch", "replace"])
def test_timed_workloads_preserve_siblings_indexes_unique_claims_and_references(
    benchmarks, db, workload, method
):
    b = benchmarks.support
    row = b.measure(db, 3, method, 10, workload)
    assert row["iterations"] == 10
    assert row["operations_per_iteration"]["attempts"] == 1
    root = db.get(b.model_for(workload), "W")
    expected = b.changes_for(workload, 15)
    for name, value in expected.items():
        assert getattr(root.bins["A"].lots["0"], name) == value
    assert root.bins["A"].lots["1"].quantity == Decimal(0)
    assert db.check_indexes(b.model_for(workload), limit=10).issues == ()
    if workload != "unindexed":
        with pytest.raises(ReferenceViolation):
            db.delete(b.Product, "P0")
        duplicate = root.bins["A"].lots["1"].model_copy(update={"id": "duplicate"})
        with pytest.raises(UniqueViolation):
            db.insert_child(
                b.TrackedWarehouse, "W", collection=("bins", "A", "lots"), record=duplicate
            )


@pytest.mark.parametrize("workload", ["indexed", "unique", "reference"])
def test_bare_direct_assignment_is_rejected_for_workloads_that_need_secondary_updates(
    benchmarks, db, workload
):
    with pytest.raises(ValueError, match="only comparable"):
        benchmarks.support.operation(db, "direct", workload)


def test_attempt_counter_counts_callback_retries_separately_from_committed_calls(benchmarks):
    class RestartingStore(MemoryStore):
        def atomic(self, callback):
            def restart():
                callback()
                raise RuntimeError("simulated native rollback")

            with pytest.raises(RuntimeError):
                super().atomic(restart)
            return super().atomic(callback)

    store = benchmarks.support.CountedStore(RestartingStore())
    assert store.atomic(lambda: 42) == 42
    assert store.counts == {"atomic": 1, "attempts": 2}


@pytest.mark.parametrize("pattern", ["separate_roots", "separate_children", "same_child"])
@pytest.mark.parametrize("method", ["patch", "replace"])
def test_contention_workload_checks_expected_increments_for_each_ownership_pattern(
    benchmarks, db, pattern, method
):
    c = benchmarks.contention
    c.seed(db, "indexed", pattern, 2, 3)
    for index in range(2):
        key, lot = c.location(pattern, index)
        mutate = c.increment_operation(db, benchmarks.support.TrackedWarehouse, key, lot, method)
        for _ in range(4):
            mutate()
    c.verify(db, "indexed", pattern, 2, 4, 3)


def test_profile_output_is_separate_and_contains_framework_functions(benchmarks, db):
    result = benchmarks.profiles.measure(db, "indexed", "patch", 3, 5)
    assert result["iterations"] == 5
    assert any("mccann_hub/yhmf/" in row["function"] for row in result["top_cumulative"])
    assert all(row["total_calls"] > 0 for row in result["top_self"])
