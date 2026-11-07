# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import Database, NativeStore, Patch, Reference, Registry
from mccann_hub.yhmf.errors import (
    MutationError,
    ReferenceViolation,
    TransactionError,
    UniqueViolation,
)
from tests.adversarial_support import dump
from tests.mutation_models import Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore

ROOTS = (Warehouse.__global__, Product.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef")


def change(child, **changes):
    return Patch(Warehouse, "W", at=("bins", "A", "lots", child), changes=changes)


@pytest.fixture
def db(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse(3))
    db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"serial": "A"})
    db.patch(Warehouse, "W", at=("bins", "A", "lots", "1"), changes={"serial": "B"})
    return db


def test_swapping_unique_child_values_is_one_valid_transition(db):
    with pytest.raises(UniqueViolation):
        db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"serial": "B"})

    def swap(tx):
        assert tx.get(Warehouse, "W").bins["A"].lots["0"].serial == "A"
        tx.patch_many([change("0", serial="B"), change("1", serial="A")])
        assert tx.get(Warehouse, "W").bins["A"].lots["0"].serial == "B"

    db.atomic(swap)
    assert db.get(Warehouse, "W").bins["A"].lots["1"].serial == "A"
    assert db.check_indexes(Warehouse).issues == ()


def test_grouped_patch_can_span_roots_and_models(db):
    db.insert(Product(id="P", name="Before"))
    db.insert(warehouse(1).model_copy(update={"id": "OTHER"}))
    db.patch_many(
        [
            change("0", serial="B"),
            change("1", serial="A"),
            Patch(Product, "P", changes={"name": "After"}),
            Patch(Warehouse, "OTHER", changes={"name": "Changed"}),
        ]
    )
    assert db.get(Product, "P").name == "After"
    assert db.get(Warehouse, "OTHER").name == "Changed"
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.parametrize("kind", ["unique", "validation", "reference", "duplicate", "wrong_key"])
def test_rejected_groups_preserve_all_data_and_secondary_structures(db, kind):
    before = dump(db.store, ROOTS)
    invalid, error = {
        "unique": (change("2", serial="A"), UniqueViolation),
        "validation": (change("2", quantity=Decimal("-1")), ValidationError),
        "reference": (change("2", product=Reference[Product](key="missing")), ReferenceViolation),
        "duplicate": (change("0", note="twice"), MutationError),
        "wrong_key": (Patch(Warehouse, True, changes={"name": "No"}), ValueError),
    }[kind]
    with pytest.raises(error):
        db.patch_many([change("0", serial="B"), change("1", serial="A"), invalid])
    assert dump(db.store, ROOTS) == before


@pytest.mark.parametrize("items", [[], [None], [change("0", note="No")] * 101, "bad"])
def test_group_bounds_and_request_types_are_checked_before_writes(db, items):
    before = dump(db.store, ROOTS)
    with pytest.raises(MutationError):
        db.patch_many(items)
    assert dump(db.store, ROOTS) == before


def test_caught_group_failure_poisoning_rolls_back_prior_writes(db):
    before = dump(db.store, ROOTS)

    def rejected(tx):
        tx.patch(Warehouse, "W", changes={"name": "No"})
        try:
            tx.patch_many([change("0", serial="B"), change("1", serial="B")])
        except UniqueViolation:
            pass

    with pytest.raises(TransactionError):
        db.atomic(rejected)
    assert dump(db.store, ROOTS) == before


def test_reference_swaps_preserve_counts_edges_and_restrict_deletion(db):
    for key in ("P", "Q"):
        db.insert(Product(id=key, name=key))
    db.patch_many(
        [
            change("0", product=Reference[Product](key="P")),
            change("1", product=Reference[Product](key="Q")),
        ]
    )
    db.patch_many(
        [
            change("0", product=Reference[Product](key="Q")),
            change("1", product=Reference[Product](key="P")),
        ]
    )
    for key in ("P", "Q"):
        inverse = db.referrers(Reference[Product](key=key), source=Warehouse)
        assert len(inverse) == 1
        with pytest.raises(ReferenceViolation):
            db.delete(Product, key)
    assert db.check_indexes(Warehouse).issues == ()


def test_group_changes_only_selected_nodes_without_loading_siblings(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(warehouse(1000))
    observed.operations.clear()
    db.patch_many([change("0", note="one"), change("1", note="two")])
    assert len(observed.operations) < 100
    assert not any(path.subscripts[4:5] == (b"s:999",) for _, path in observed.operations)
    writes = [(kind, path) for kind, path in observed.operations if kind in {"set", "delete"}]
    assert len(writes) == 2
    assert all(path.subscripts[-1] == b"note" for _, path in writes)


def test_group_input_is_copied_once_before_transaction_attempts(db, monkeypatch):
    values = {"note": "original"}
    requests = [Patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes=values)]
    real_atomic = db.store.atomic

    def repeat_after_rollback(callback):
        def discarded():
            callback()
            values["note"] = "changed"
            requests.append(change("1", note="extra"))
            raise RuntimeError("discard first attempt")

        with pytest.raises(RuntimeError, match="discard first"):
            real_atomic(discarded)
        return real_atomic(callback)

    with monkeypatch.context() as patch:
        patch.setattr(db.store, "atomic", repeat_after_rollback)
        db.patch_many(requests)
    lots = db.get(Warehouse, "W").bins["A"].lots
    assert lots["0"].note == "original" and lots["1"].note == ""


@pytest.mark.native
def test_native_grouped_swap_restarts_without_publishing_partial_claims(db):
    if not isinstance(db.store, NativeStore):
        pytest.skip("Requires native transaction restarts")
    yottadb = pytest.importorskip("yottadb")
    attempts = []

    def swap(tx):
        attempts.append(tx.get(Warehouse, "W").bins["A"].lots["0"].serial)
        tx.patch_many([change("0", serial="B"), change("1", serial="A")])
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(swap)
    assert attempts == ["A", "A"]
    assert db.get(Warehouse, "W").bins["A"].lots["1"].serial == "A"
    assert db.check_indexes(Warehouse).issues == ()
