# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace

import pytest

from mccann_hub.yhmf import Database, Field, Model, Reference, Registry
from mccann_hub.yhmf.errors import MutationError, NotFound, SchemaError, TransactionError
from tests.mutation_models import Address, Lot, Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore
from tests.test_schema_contracts import seed_legacy


def test_typed_path_reads_one_nested_owner_and_preserves_tuple_api(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse())
    path = db.path(Warehouse, lambda w: w.bins["A"].lots["0"])
    value = db.get(Warehouse, "W", at=path)
    assert isinstance(value, Lot) and value.id == "0"
    assert value == db.get(Warehouse, "W", at=path.at)
    assert db.get(Warehouse, "W", at=db.path(Warehouse, lambda w: w)).id == "W"
    assert db.get(Warehouse, "W", at=db.path(Warehouse, lambda w: w.address)) == Address(
        city="Chicago"
    )


def test_nullable_path_selection_does_not_evaluate_record_values(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(Warehouse(id="W"))
    path = db.path(Warehouse, lambda w: w.address)
    assert path.target is Address
    with pytest.raises(NotFound):
        db.get(Warehouse, "W", at=path)


@pytest.mark.parametrize(
    "select",
    [
        lambda w: w.misspelled,
        lambda w: w.name,
        lambda w: w.bins,
        lambda w: w.bins[1],
        lambda w: Warehouse(id="outside"),
        lambda w: w.address if w.address else w,
    ],
)
def test_invalid_selectors_are_rejected_without_storage_access(store, select):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    with pytest.raises(SchemaError):
        db.path(Warehouse, select)
    assert observed.operations == []


def test_typed_path_binding_and_target_are_checked_at_runtime(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse())
    path = db.path(Warehouse, lambda w: w.bins["A"].lots["0"])
    with pytest.raises(SchemaError):
        db.get(Product, "P", at=path)
    with pytest.raises(SchemaError):
        db.get(Warehouse, "W", at=replace(path, fingerprint=b"forged"))
    with pytest.raises(MutationError, match="target"):
        db.get(Warehouse, "W", at=replace(path, target=Product))
    expired = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError, match="expired"):
        expired.path(Warehouse, lambda w: w.address)


def test_selectors_do_not_load_unrelated_siblings(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(warehouse(1000))
    observed.operations.clear()
    path = db.path(Warehouse, lambda w: w.bins["A"].lots["0"])
    assert observed.operations == []
    assert db.get(Warehouse, "W", at=path).id == "0"
    assert len(observed.operations) < 40
    assert not any(path.subscripts[4:5] == (b"s:999",) for _, path in observed.operations)


@pytest.mark.parametrize("legacy", [False, True])
def test_reference_factory_keeps_existing_generic_identity_and_schema_bytes(store, legacy):
    registry = Registry(Product, Warehouse)
    before = registry.spec(Warehouse).snapshot
    product = registry.reference(Product, lambda p: p.id)
    reference = product("P")
    assert type(reference) is Reference[Product]
    assert registry.spec(Warehouse).snapshot == before
    if legacy:
        seed_legacy(store, Product(id="P", name="Product"))
    db = Database(store, registry)
    if not legacy:
        db.insert(Product(id="P", name="Product"))
    item = warehouse()
    item.bins["A"].lots["0"].product = reference
    db.insert(item)
    assert db.resolve(product("P")).name == "Product"
    assert len(db.referrers(reference, source=Warehouse)) == 1
    assert db.check_indexes(Warehouse).issues == ()
    with pytest.raises(SchemaError):
        product(1)


def test_factory_selects_only_the_registered_primary_key():
    registry = Registry(Product)
    with pytest.raises(SchemaError):
        registry.reference(Product, lambda p: p.name)

    class Numeric(Model):
        __global__ = "^YHMFTestOrder"
        id: int = Field(primary_key=True)

    registry.register(Numeric)
    numeric = registry.reference(Numeric, lambda value: value.id)
    assert numeric(42) == Reference[Numeric](key=42)
    for invalid in ("42", True):
        with pytest.raises(SchemaError):
            numeric(invalid)
