# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import NotFound
from tests.mutation_models import Lot, Product, Warehouse, warehouse
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


@pytest.mark.adversarial
@pytest.mark.parametrize("swap_first", [False, True])
def test_native_grouped_swap_keeps_claims_exclusive_against_another_process(db, swap_first):
    for child, serial in (("0", "A"), ("1", "B")):
        db.patch(Warehouse, "W", at=("bins", "A", "lots", child), changes={"serial": serial})
    db.insert_child(Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id="2"))
    swap, claim = ("swap", "unused", "unused"), ("serial", "2", "A")
    commands = (swap, claim) if swap_first else (claim, swap)
    expected = ["committed", "UniqueViolation"] if swap_first else ["UniqueViolation", "committed"]
    assert race(*commands) == expected
    lots = db.get(Warehouse, "W").bins["A"].lots
    assert (lots["0"].serial, lots["1"].serial, lots["2"].serial) == ("B", "A", None)
    assert db.check_indexes(Warehouse).issues == ()


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


@pytest.mark.parametrize("delete_first", [False, True])
@pytest.mark.parametrize("operation", ["delete_child", "delete_bin"])
def test_native_child_or_ancestor_delete_races_with_patch(db, delete_first, operation):
    delete = (operation, "0", "unused")
    patch = ("quantity", "0", "1")
    commands = (delete, patch) if delete_first else (patch, delete)
    expected = ["committed", "NotFound"] if delete_first else ["committed", "committed"]
    assert race(*commands) == expected
    with pytest.raises(NotFound):
        db.get(Warehouse, "W", at=("bins", "A", "lots", "0"))
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_native_concurrent_insertions_preserve_collection_count_and_siblings(db):
    assert race(("insert", "new1", "one"), ("insert", "new2", "two")) == ["committed", "committed"]
    lots = db.get(Warehouse, "W").bins["A"].lots
    assert lots.keys() == {"0", "1", "new1", "new2"}
    assert lots["new1"].note == "one" and lots["new2"].note == "two"
    assert db.check_indexes(Warehouse, limit=5).issues == ()


@pytest.mark.parametrize("delete_first", [False, True])
def test_native_reference_patch_and_target_deletion_remain_atomic(db, delete_first):
    db.insert(Product(id="P", name="Target"))
    delete, link = ("delete_product", "0", "unused"), ("link_product", "0", "unused")
    assert race(*((delete, link) if delete_first else (link, delete))) == [
        "committed",
        "ReferenceViolation",
    ]
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_native_child_reference_removal_allows_concurrent_target_delete(db):
    db.insert(Product(id="P", name="Target"))
    db.patch(
        Warehouse,
        "W",
        at=("bins", "A", "lots", "0"),
        changes={"product": Reference[Product](key="P")},
    )
    assert race(("delete_referenced_child", "0", "unused"), ("delete_product", "0", "unused")) == [
        "committed",
        "committed",
    ]
    with pytest.raises(NotFound):
        db.get(Product, "P")


def test_native_child_insertion_and_deletion_restart_as_one_transaction(db):
    yottadb = importlib.import_module("yottadb")
    attempts = []

    def change(tx):
        attempts.append(tx.get(Warehouse, "W").bins["A"].lots.keys())
        tx.insert_child(
            Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id="new", serial="new")
        )
        tx.delete_child(Warehouse, "W", at=("bins", "A", "lots", "0"))
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(change)
    assert attempts == [{"0", "1"}, {"0", "1"}]
    assert db.get(Warehouse, "W").bins["A"].lots.keys() == {"1", "new"}
    assert db.check_indexes(Warehouse, limit=5).issues == ()
