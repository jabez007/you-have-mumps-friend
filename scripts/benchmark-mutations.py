# SPDX-License-Identifier: GPL-3.0-or-later
"""Native latency, contention, and profiling in the disposable test database."""

import argparse
import json
import os

import benchmark_contention
import benchmark_insertions
import benchmark_profiles
from benchmark_support import WORKLOADS, CountedStore, cleanup, environment, measure, setup

from mccann_hub.yhmf import NativeStore


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 10, 100, 1000])
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS, default=list(WORKLOADS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--contention-size", type=int, default=100)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Run only inside scripts/test-native.sh with YHMF_BENCHMARK=1")
    if args.iterations < 10 or any(not 1 <= size <= 1000 for size in args.sizes):
        parser.error("Use at least 10 iterations and sizes from 1 to 1000")
    if not 2 <= args.workers <= 8 or not args.workers <= args.contention_size <= 1000:
        parser.error("Use 2 to 8 workers and a contention size from worker count to 1000")
    return args


def serial_rows(db, args):
    rows = []
    for workload in args.workloads:
        methods = (
            ("direct", "patch", "replace") if workload == "unindexed" else ("patch", "replace")
        )
        for size in args.sizes:
            for method in methods:
                rows.append(measure(db, size, method, args.iterations, workload))
    return rows


def contention_rows(db, args):
    return [
        benchmark_contention.measure(
            db, workload, method, pattern, args.workers, args.contention_size, args.iterations
        )
        for workload in args.workloads
        if workload in {"unindexed", "indexed"}
        for method in ("patch", "replace")
        for pattern in benchmark_contention.PATTERNS
    ]


def profiles(db, args):
    rows = [
        benchmark_profiles.measure(db, workload, "patch", max(args.sizes), min(args.iterations, 25))
        for workload in args.workloads
    ]
    if "indexed" in args.workloads:
        rows.append(
            benchmark_profiles.measure(
                db, "indexed", "replace", min(max(args.sizes), 100), min(args.iterations, 25)
            )
        )
    return rows


def main():
    args = arguments()
    if args.worker:
        benchmark_contention.worker(json.loads(args.worker))
        return
    store = CountedStore(NativeStore())
    cleanup(store)
    try:
        db = setup(store)
        serial = serial_rows(db, args)
        contention = contention_rows(db, args)
        profiled = profiles(db, args)
        insertions = [
            benchmark_insertions.measure(db, size, method, args.iterations, pattern)
            for size in sorted({min(size, 999) for size in args.sizes})
            for pattern in ("target_collection", "unrelated_collection")
            for method in ("direct", "insert_child", "replace")
        ]
        print(
            "YHMF_BENCHMARK="
            + json.dumps(
                {
                    "format_version": 2,
                    **environment(),
                    "warmup_iterations": 5,
                    "rows": serial,
                    "contention": contention,
                    "profiles": profiled,
                    "insertions": insertions,
                },
                sort_keys=True,
            )
        )
    finally:
        cleanup(store)


if __name__ == "__main__":
    main()
