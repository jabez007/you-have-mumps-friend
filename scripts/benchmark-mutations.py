# SPDX-License-Identifier: GPL-3.0-or-later
"""Descriptive native mutation benchmark; requires the disposable test database."""

import argparse
import json
import os
import platform

import pydantic
from benchmark_support import CountedStore, Warehouse, measure

from mccann_hub.yhmf import Database, NativeStore, Path, Registry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 10, 100, 1000])
    args = parser.parse_args()
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Run only inside scripts/test-native.sh with YHMF_BENCHMARK=1")
    if args.iterations < 10 or any(not 1 <= size <= 1000 for size in args.sizes):
        parser.error("Use at least 10 iterations and sizes from 1 to 1000")
    store = CountedStore(NativeStore())
    db = Database(store, Registry(Warehouse))
    root = Path(Warehouse.__global__)
    schema = Path("^YHMFSchema", (b"YHMFBenchWarehouse",))
    store.delete_tree(root)
    store.delete_tree(schema)
    try:
        rows = [
            measure(db, size, method, args.iterations)
            for size in args.sizes
            for method in ("direct", "patch", "replace")
        ]
        import yottadb

        print(
            "YHMF_BENCHMARK="
            + json.dumps(
                {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "pydantic": pydantic.__version__,
                    "yottadb": yottadb.release(),
                    "warmup_iterations": 5,
                    "indexed_quantity": False,
                    "rows": rows,
                },
                sort_keys=True,
            )
        )
    finally:
        store.delete_tree(root)
        store.delete_tree(schema)


if __name__ == "__main__":
    main()
