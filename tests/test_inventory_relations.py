# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from pydantic import ValidationError

from examples.inventory import MODELS, Product, Supplier, SupplierProduct
from mccann_hub.yhmf import Database, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation


def test_many_to_many_supplier_memberships_use_owned_links_and_native_inverse_entries(store):
    db = Database(store, Registry(*MODELS))
    for key in ("P1", "P2"):
        db.insert(Product(id=key, sku=key, name=key))
    for key in ("S1", "S2"):
        db.insert(Supplier(id=key, name=key))
        db.insert_child(
            Supplier,
            key,
            collection=("products",),
            record=SupplierProduct(id="P1", product=Reference[Product](key="P1")),
        )
    db.insert_child(
        Supplier,
        "S1",
        collection=("products",),
        record=SupplierProduct(id="P2", product=Reference[Product](key="P2")),
    )
    hits = db.referrers(Reference[Product](key="P1"), source=Supplier, limit=1)
    assert hits[0].root_key == "S1" and hits[0].at == ("products", "P1")
    rest = db.referrers(Reference[Product](key="P1"), source=Supplier, after=hits[-1].cursor)
    assert len(rest) == 1 and rest[0].root_key == "S2"
    assert type(rest[0].record) is SupplierProduct
    assert [item.id for item in db.children(Supplier, "S1", collection=("products",))] == [
        "P1",
        "P2",
    ]
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P1")
    db.delete_child(Supplier, "S1", at=("products", "P1"))
    assert [
        hit.root_key for hit in db.referrers(Reference[Product](key="P1"), source=Supplier)
    ] == ["S2"]
    db.delete(Supplier, "S2")
    assert db.referrers(Reference[Product](key="P1"), source=Supplier) == []
    assert db.delete(Product, "P1")
    assert db.get(Product, "P2").name == "P2"
    assert db.check_indexes(Supplier).issues == ()


def test_membership_validation_keeps_the_child_key_bound_to_its_product(store):
    db = Database(store, Registry(*MODELS))
    for key in ("P1", "P2"):
        db.insert(Product(id=key, sku=key, name=key))
    db.insert(Supplier(id="S", name="Supplier"))
    invalid = SupplierProduct.model_construct(id="P1", product=Reference[Product](key="P2"))
    with pytest.raises(ValidationError):
        db.insert_child(Supplier, "S", collection=("products",), record=invalid)
    assert db.count(Supplier, "S", collection=("products",)) == 0
