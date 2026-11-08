# SPDX-License-Identifier: GPL-3.0-or-later
"""M creation/removal of empty owned branches matches Python's hierarchy."""

from decimal import Decimal

import pytest

from examples.inventory import MODELS, Balance, Bin, Location, Lot, Product, Warehouse
from mccann_hub.yhmf import Database, MemoryStore, Reference, Registry
from tests import test_m_owned_writers
from tests.adversarial_support import dump

m_writer = test_m_owned_writers.m_writer
owned_writer = test_m_owned_writers.owned_writer
variant_writer = test_m_owned_writers.variant_writer


@pytest.mark.native
@pytest.mark.parametrize(
    "scope,model,collection",
    [("location", Location, ("locations",)), ("bin", Bin, ("locations", "DOCK", "bins"))],
)
def test_empty_nonleaf_insert_and_delete_match_python(
    store, owned_writer, scope, model, collection
):
    db, run, _, _ = owned_writer
    expected_store = MemoryStore()
    roots = test_m_owned_writers.namespaces()
    for path, value in dump(store, roots).items():
        expected_store.set(path, value)
    expected = Database(expected_store, Registry(*MODELS))
    assert run("emptyinsert", {"_scope": scope, "id": "NEW"}) == "ok"
    expected.insert_child(Warehouse, "MAIN", collection=collection, record=model(id="NEW"))
    assert dump(store, roots) == dump(expected_store, roots)
    assert db.check_indexes(Warehouse).issues == ()
    assert run("emptydelete", {"_scope": scope, "id": "NEW"}) == "ok"
    expected.delete_child(Warehouse, "MAIN", at=collection + ("NEW",))
    assert dump(store, roots) == dump(expected_store, roots)
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_populated_nonleaf_delete_cleans_indexes_references_and_counts(store, owned_writer):
    db, run, _, _ = owned_writer
    db.insert_child(
        Warehouse,
        "MAIN",
        collection=("locations", "DOCK", "bins", "B1", "balances"),
        record=Balance(
            id="LOT-001",
            lot=Reference[Lot](key="LOT-001"),
            product=Reference[Product](key="WIDGET"),
            quantity=Decimal(2),
        ),
    )
    roots = test_m_owned_writers.namespaces()
    expected_store = MemoryStore()
    for path, value in dump(store, roots).items():
        expected_store.set(path, value)
    expected = Database(expected_store, Registry(*MODELS))
    assert run("emptydelete", {"_scope": "bin", "id": "B1"}) == "ok"
    expected.delete_child(Warehouse, "MAIN", at=("locations", "DOCK", "bins", "B1"))
    assert dump(store, roots) == dump(expected_store, roots)
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_empty_child_creation_cannot_bypass_minimum_collection_length(store, variant_writer):
    db, run, _, _ = variant_writer(
        test_m_owned_writers.BoundedWarehouse,
        test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"}),
    )
    roots = test_m_owned_writers.namespaces()
    before = dump(store, roots)
    assert run("emptyinsert", {"_scope": "bin", "id": "NEW"}) == "collection_limit"
    assert dump(store, roots) == before
    assert db.check_indexes(test_m_owned_writers.BoundedWarehouse).issues == ()


@pytest.mark.native
def test_empty_child_rejects_supplied_derived_totals_and_collections(store, owned_writer):
    _, run, _, _ = owned_writer
    roots = test_m_owned_writers.namespaces()
    before = dump(store, roots)
    assert (
        run("emptyinsert", {"_scope": "location", "id": "NEW", "stored_on_hand": "0"}) == "invalid"
    )
    assert run("emptyinsert", {"_scope": "bin", "id": "NEW", "balances": "c:0"}) == "invalid"
    assert dump(store, roots) == before
