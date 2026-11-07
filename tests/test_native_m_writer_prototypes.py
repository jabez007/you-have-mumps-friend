# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import NativeStore, Path


@pytest.fixture
def m_probe(store, tmp_path):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires real M execution")
    source = FilePath(__file__).with_name("m") / "YHMFWPROBE.m"
    (tmp_path / source.name).write_bytes(source.read_bytes())
    environment = dict(os.environ)
    environment["ydb_routines"] = str(tmp_path) + " " + environment["ydb_routines"]

    def run(label):
        return subprocess.run(
            [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", f"{label}^YHMFWPROBE"],
            env=environment,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        ).stdout.strip()

    return run, environment


@pytest.mark.native
def test_native_trigger_maintenance_rollback_and_parent_kill(store, m_probe, tmp_path):
    run, environment = m_probe
    definition = tmp_path / "probe.trg"
    definition.write_text(
        '+^YHMFTestStorage("triggerRecords",id=:,"value") -commands=SET '
        '-name=YHMFTS -xecute="do setTrigger^YHMFWPROBE"\n'
        '+^YHMFTestStorage("triggerRecords",id=:,"value") -commands=KILL '
        '-name=YHMFTK -xecute="do killTrigger^YHMFWPROBE"\n'
    )
    cleanup = tmp_path / "cleanup.trg"
    cleanup.write_text("-YHMFTS\n-YHMFTK\n")
    mupip = str(FilePath(environment["ydb_dist"]) / "mupip")
    root = Path("^YHMFTestStorage")

    def triggers(path):
        result = subprocess.run(
            [mupip, "trigger", f"-triggerfile={path}"],
            env=environment,
            text=True,
            capture_output=True,
            check=True,
            timeout=20,
        )
        assert "ERROR" not in result.stdout + result.stderr

    try:
        triggers(definition)
        run("triggerCreate")
        assert store.get(root.child(b"triggerIndex", b"s:one", b"1")) == b"1"
        assert store.get(root.child(b"sawIdentity")) == b"0"
        run("triggerPatch")
        assert store.get(root.child(b"triggerIndex", b"s:one", b"1")) is None
        assert store.get(root.child(b"triggerIndex", b"s:two", b"1")) == b"1"
        run("triggerRollback")
        assert store.get(root.child(b"triggerRecords", b"1", b"value")) == b"s:two"
        assert store.get(root.child(b"triggerIndex", b"s:rolledBack", b"1")) is None
        assert store.get(root.child(b"setCalls")) == b"2"
        run("triggerLeafKill")
        assert store.get(root.child(b"killCalls")) == b"1"
        assert store.get(root.child(b"triggerIndex", b"s:two", b"1")) is None
        run("triggerPatch")
        run("triggerParentKill")
        assert store.children(root.child(b"triggerRecords"), limit=1) == ()
        assert store.get(root.child(b"killCalls")) == b"1"
        assert store.get(root.child(b"triggerIndex", b"s:two", b"1")) == b"1"
    finally:
        triggers(cleanup)


@pytest.mark.native
def test_explicit_m_routine_prototype_validates_before_transactional_index_writes(store, m_probe):
    run, _ = m_probe
    root = Path("^YHMFTestStorage")
    assert run("apiCreate") == "ok"
    assert store.get(root.child(b"apiRecords", b"1", b"value")) == b"s:one"
    assert store.get(root.child(b"apiIndex", b"s:one", b"1")) == b"1"
    assert run("apiCreate") == "exists"
    assert run("apiPatch") == "ok"
    assert store.get(root.child(b"apiIndex", b"s:one", b"1")) is None
    assert store.get(root.child(b"apiIndex", b"s:two", b"1")) == b"1"
    assert run("apiInvalid") == "invalid"
    assert store.get(root.child(b"apiRecords", b"1", b"value")) == b"s:two"
    assert run("apiDelete") == "ok"
    assert store.children(root.child(b"apiRecords"), limit=1) == ()
    assert store.children(root.child(b"apiIndex"), limit=1) == ()
    assert run("apiDelete") == "missing"
