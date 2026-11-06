# SPDX-License-Identifier: GPL-3.0-or-later
"""cProfile summaries separated from the unprofiled latency samples."""

import cProfile
import pstats
from pathlib import Path

from benchmark_support import make_warehouse, operation, verify

from mccann_hub.yhmf import Database


def _name(filename, line, function):
    for prefix in ("/src/", "/site-packages/"):
        if prefix in filename:
            return f"{filename.split(prefix, 1)[1]}:{line}:{function}"
    return f"{Path(filename).name}:{line}:{function}"


def measure(db, workload, method, size, iterations):
    db.save(make_warehouse(workload, size))
    # Profile the same native adapter without the operation-counting wrapper.
    profiled_db = Database(db.store.store, db.registry)
    mutate = operation(profiled_db, method, workload)
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        for _ in range(iterations):
            mutate()
    finally:
        profiler.disable()
    verify(db, workload, size, iterations)
    stats = pstats.Stats(profiler)
    functions = [
        {
            "function": _name(*key),
            "primitive_calls": row[0],
            "total_calls": row[1],
            "self_s": round(row[2], 6),
            "cumulative_s": round(row[3], 6),
        }
        for key, row in stats.stats.items()
    ]
    return {
        "workload": workload,
        "method": method,
        "lots": size,
        "iterations": iterations,
        "profiled_total_s": round(stats.total_tt, 6),
        "guard_calls": {
            name: sum(
                row[1]
                for key, row in stats.stats.items()
                if key[2] == name and key[0].endswith(("/yhmf/owned.py", "/yhmf/registry.py"))
            )
            for name in ("_has_functions", "_nested_model_schema")
        },
        "top_cumulative": sorted(functions, key=lambda row: row["cumulative_s"], reverse=True)[:12],
        "top_self": sorted(functions, key=lambda row: row["self_s"], reverse=True)[:12],
    }
