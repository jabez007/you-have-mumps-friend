# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from examples.inventory import MODELS
from mccann_hub.yhmf import Database, Registry
from scripts.benchmark_m_writers import exported, python_case, seed, summarize, verify


@pytest.mark.parametrize("shared", [False, True])
def test_m_writer_workload_seed_preserves_independent_roots_and_reference_counts(store, shared):
    db = seed(store, 4, shared, 2)
    verify(db, 4, shared, 2)


def test_python_control_exercises_the_same_complete_mutation_cycle(store):
    rows = python_case(store, 2)
    assert {row["operation"] for row in rows} == {"insert", "patch", "delete"}
    assert all(len(row["samples_us"]) == 2 for row in rows)
    verify(Database(store, Registry(*MODELS)), 1, False, 0)


def test_native_report_preserves_each_workers_samples_and_attempts():
    worker = [
        "sample|insert|10|2|1",
        "sample|patch|20|0|2",
        "sample|delete|30|0|1",
        "cache|3",
        "elapsed|100",
    ]
    rows = summarize(
        [worker, worker],
        method="m_cached",
        workload="hot",
        workers=2,
        shared=True,
        iterations=1,
        elapsed=0.5,
    )
    assert [row["samples_us"] for row in rows] == [[10, 10], [20, 20], [30, 30]]
    assert [row["sha256_calls"] for row in rows] == [4, 0, 0]
    assert [row["tp_retries"] for row in rows] == [0, 2, 0]


def test_uncached_control_bypasses_only_memoization(store):
    db = Database(store, Registry(*MODELS))
    cached, control = exported(db), exported(db, uncached=True)
    assert control == cached.replace("$$locationhash(location)", "$$sha256(location)")
