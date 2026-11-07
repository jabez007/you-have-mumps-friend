# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Registry
from tests.native_insertion_diagnostics import Parent
from tests.test_native_schema_safety import ready

pytestmark = pytest.mark.native
STATS = {
    "TTW",
    "TTR",
    "TBW",
    "TBR",
    "SET",
    "GET",
    "ORD",
    *(f"TC{i}" for i in range(5)),
    *(f"TR{i}" for i in range(5)),
}


def _dse(command):
    result = subprocess.run(
        [str(FilePath(os.environ["ydb_dist"]) / "dse")],
        input=command + "\nexit\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert not re.search(r"%(?:YDB|GTM|DSE|CLI)-(?:E|F)-", output), output
    return output


def _snapshot(keys, tmp_path):
    source = FilePath(__file__).with_name("m") / "YHMFSTATS.m"
    (tmp_path / source.name).write_bytes(source.read_bytes())
    env = dict(os.environ)
    env["ydb_routines"] = str(tmp_path) + " " + env["ydb_routines"]
    result = subprocess.run(
        [str(FilePath(env["ydb_dist"]) / "yottadb"), "-run", "YHMFSTATS"],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    stats = {}
    for part in result.stdout.strip().split(","):
        name, _, value = part.partition(":")
        if name in STATS:
            stats[name] = int(value)
    assert STATS <= stats.keys(), result.stdout
    locations = {}
    for key in keys:
        for tail in (("items",), ("%m", "layout")):
            parts = ",".join('"' + name + '"' for name in ("s:" + key, *tail))
            expression = f"^YHMFTestWarehouse({parts})"
            quoted = '"' + expression.replace('"', '""') + '"'
            locations[expression] = _dse("find -key=" + quoted)
    expression = '^YHMFSchema("YHMFTestWarehouse")'
    locations[expression] = _dse('find -key="' + expression.replace('"', '""') + '"')
    header = _dse("dump -fileheader")
    relevant = [
        line.strip()
        for line in header.splitlines()
        if any(term in line.lower() for term in ("blkmod", "block size"))
    ]
    assert any("blkmod" in line.lower() for line in relevant), header
    return {"gvstat": stats, "block_locations": locations, "header": relevant}


def _workers(scenario, keys, iterations):
    workers = []
    try:
        for index, key in enumerate(keys):
            workers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(FilePath(__file__).with_name("native_insertion_diagnostics.py")),
                        scenario,
                        str(index),
                        key,
                        str(iterations),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for worker in workers:
            ready(worker)
        start = time.perf_counter()
        if scenario != "pair":
            for worker in workers:
                worker.stdin.write("go\n")
                worker.stdin.flush()
        results = []
        for worker in workers:
            output, error = worker.communicate("go\n" if scenario == "pair" else None, timeout=30)
            assert worker.returncode == 0, error
            results.append(json.loads(output))
        return results, time.perf_counter() - start
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)


def _addresses(values):
    return {(global_name, tuple(parts)) for global_name, parts in values}


@pytest.mark.parametrize("padding", [0, 16384])
@pytest.mark.parametrize("scenario", ["pair", "burst_separate", "burst_shared"])
def test_diagnose_native_insertion_conflicts(store, tmp_path, capsys, padding, scenario):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native engine diagnostics")
    import yottadb

    count, iterations = (2, 1) if scenario == "pair" else (4, 40)
    keys = ["R0" if scenario == "burst_shared" else f"R{i}" for i in range(count)]
    unique_keys = list(dict.fromkeys(keys))
    db = Database(store, Registry(Parent))
    for key in unique_keys:
        db.insert(Parent(id=key, padding="P" * padding))
    before = _snapshot(unique_keys, tmp_path)
    results, elapsed = _workers(scenario, keys, iterations)
    after = _snapshot(unique_keys, tmp_path)
    overlap = set()
    for i, result in enumerate(results):
        for other in results[i + 1 :]:
            overlap |= (_addresses(result["reads"]) & _addresses(other["writes"])) | (
                _addresses(other["reads"]) & _addresses(result["writes"])
            )
    assert bool(overlap) == (scenario == "burst_shared")
    for key in unique_keys:
        expected = {f"{i}:{j}" for i in range(count) for j in range(iterations) if keys[i] == key}
        assert db.count(Parent, key, collection=("items",)) == len(expected)
        assert {
            row.id for row in db.children(Parent, key, collection=("items",), limit=1000)
        } == expected
    commits = count * iterations
    report = {
        "engine": yottadb.release(),
        "journaling": os.environ.get("YHMF_JOURNALING") == "1",
        "scenario": scenario,
        "padding_bytes": padding,
        "commits": commits,
        "elapsed_seconds": elapsed,
        "attempts_per_commit": sum(result["counts"]["attempts"] for result in results) / commits,
        "logical_read_write_overlap": [[name, list(parts)] for name, parts in sorted(overlap)],
        "before": before,
        "after": after,
        "gvstat_delta": {name: after["gvstat"][name] - before["gvstat"][name] for name in STATS},
        "workers": results,
    }
    with capsys.disabled():
        print("YHMF_INSERTION_DIAGNOSTIC " + json.dumps(report, sort_keys=True), flush=True)
