# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from mccann_hub.yhmf.errors import NotFound
from tests.mutation_models import Product, Warehouse, warehouse
from tests.test_native_schema_safety import ready

pytestmark = pytest.mark.native
WORKER = FilePath(__file__).with_name("native_mutations.py")


@pytest.fixture
def db():
    if os.environ.get("YHMF_TEST_NATIVE") != "1":
        pytest.skip("Requires a disposable YottaDB database")
    store = NativeStore()
    roots = [
        Path(name)
        for name in (
            "^YHMFTestWarehouse",
            "^YHMFTestProduct",
            "^YHMFSchema",
            "^YHMFIndex",
            "^YHMFUnique",
            "^YHMFRef",
        )
    ]
    for root in roots:
        store.delete_tree(root)
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse(2))
    yield db
    for root in roots:
        store.delete_tree(root)


def race(first, second):
    workers = []
    try:
        for command in (first, second):
            workers.append(
                subprocess.Popen(
                    [sys.executable, str(WORKER), *command],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in workers:
            ready(process)
        results = []
        for process in workers:
            output, error = process.communicate("go\n", timeout=20)
            assert process.returncode == 0, error
            results.append(output.strip())
        return results
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize(
    "second", [("quantity", "1", "2"), ("note", "0", "Changed"), ("quantity", "0", "2")]
)
def test_native_independent_and_same_field_patches_preserve_committed_state(db, second):
    assert race(("quantity", "0", "1"), second) == ["committed", "committed"]
    lots = db.get(Warehouse, "W").bins["A"].lots
    if second[0] == "note":
        assert lots["0"].quantity == 1 and lots["0"].note == "Changed"
    elif second[1] == "1":
        assert lots["0"].quantity == 1 and lots["1"].quantity == 2
    else:
        assert lots["0"].quantity == 2
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_native_unique_patch_race_commits_one_claim(db):
    assert race(("serial", "0", "shared"), ("serial", "1", "shared")) == [
        "committed",
        "UniqueViolation",
    ]
    lots = db.get(Warehouse, "W").bins["A"].lots
    assert lots["0"].serial == "shared" and lots["1"].serial is None
    assert db.check_indexes(Warehouse, limit=5).issues == ()


@pytest.mark.parametrize("delete_first", [False, True])
def test_native_parent_delete_and_nested_patch_serialize(db, delete_first):
    delete = ("delete_parent", "0", "unused")
    patch = ("quantity", "0", "1")
    commands = (delete, patch) if delete_first else (patch, delete)
    expected = ["committed", "NotFound"] if delete_first else ["committed", "committed"]
    assert race(*commands) == expected
    with pytest.raises(NotFound):
        db.get(Warehouse, "W")


def test_native_patch_restart_and_rollback_keep_data_and_secondary_entries_together(db):
    yottadb = importlib.import_module("yottadb")
    observed = []

    def update(tx):
        at = ("bins", "A", "lots", "0")
        observed.append(tx.get(Warehouse, "W", at=at).quantity)
        tx.patch(Warehouse, "W", at=at, changes={"quantity": Decimal("3"), "serial": "claimed"})
        if len(observed) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(update)
    assert observed == [Decimal("0"), Decimal("0")]
    assert db.get(Warehouse, "W").bins["A"].lots["0"].quantity == Decimal("3")
    assert db.check_indexes(Warehouse, limit=5).issues == ()

    def abort(tx):
        tx.patch(
            Warehouse,
            "W",
            at=("bins", "A", "lots", "0"),
            changes={"quantity": Decimal("4"), "serial": "other"},
        )
        raise RuntimeError("abort patch")

    with pytest.raises(RuntimeError):
        db.atomic(abort)
    assert db.get(Warehouse, "W").bins["A"].lots["0"].serial == "claimed"
    assert db.check_indexes(Warehouse, limit=5).issues == ()
