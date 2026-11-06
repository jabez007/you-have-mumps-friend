# SPDX-License-Identifier: GPL-3.0-or-later
"""Default-sized regions run in a child process with an isolated global directory."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.native

WORKLOAD = """
import yottadb
from mccann_hub.yhmf import Database, Field, Model, NativeStore, Path, Registry
from mccann_hub.yhmf.errors import StorageError

class Row(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    value: str

store = NativeStore()
try:
    Database(store, Registry(Row)).insert(Row(id=1, value="sample"))
except StorageError as error:
    assert "record_size" in str(error), str(error)
    assert error.__cause__.code() == yottadb.YDB_ERR_REC2BIG
else:
    raise AssertionError("Default region unexpectedly accepted the schema")
assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None
assert not store.children(Path("^YHMFSchema"), limit=1)
assert not store.children(Path("^YHMFTestProduct"), limit=1)

try:
    store.set(Path("^YHMFTestStorage", (b"s:" + b"a" * 100,)), b"x")
except StorageError as error:
    assert "key_size" in str(error), str(error)
    assert error.__cause__.code() == yottadb.YDB_ERR_KEY2BIG
else:
    raise AssertionError("Default region unexpectedly accepted the long key")
print("default region: actionable errors and atomic rollback verified")
"""


def test_default_region_reports_size_settings_and_leaves_no_partial_installation(tmp_path):
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Requires a disposable native environment")
    dist = Path(os.environ["ydb_dist"])
    env = os.environ | {"ydb_gbldir": str(tmp_path / "default.gld")}
    subprocess.run(
        [dist / "yottadb", "-run", "GDE"],
        input=f"change -segment DEFAULT -file={tmp_path / 'default.dat'}\nexit\n",
        text=True,
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run([dist / "mupip", "create"], env=env, check=True, capture_output=True, timeout=30)
    try:
        result = subprocess.run(
            [sys.executable, "-c", WORKLOAD],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert "atomic rollback verified" in result.stdout
    finally:
        subprocess.run(
            [dist / "mupip", "rundown", "-region", "*"],
            env=env,
            capture_output=True,
            timeout=30,
        )
