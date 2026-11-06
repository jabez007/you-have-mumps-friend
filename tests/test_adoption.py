# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation, StorageError, UniqueViolation
from mccann_hub.yhmf.records import record_nodes


class Lot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(ge=0, index=True)


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str = Field(unique=True)
    lots: Children[Lot] = ChildField(key="id")
    related: "Reference[Product] | None" = None


Product.model_rebuild(_types_namespace={"Product": Product})


def raw(store, registry, record, *, metadata=False):
    spec = registry.spec(type(record))
    root = spec.path(record.id)
    nodes = record_nodes(spec, root, record)
    for path, value in nodes.items():
        if metadata or path.subscripts[1] != b"%m":
            store.set(path, value)
    return root


@pytest.mark.parametrize("metadata", [False, True])
def test_explicit_adoption_validates_owned_records_and_builds_indexes_and_references(
    store, metadata
):
    registry = Registry(Product)
    first = Product(id="one", name="First", lots={"L": Lot(id="L", quantity=Decimal("2.50"))})
    second = Product(id="two", name="Second", related=Reference[Product](key="one"))
    root = raw(store, registry, first, metadata=metadata)
    raw(store, registry, second, metadata=metadata)
    db = Database(store, registry)
    assert db.inspect_schema(Product).status == "unmanaged"
    assert db.adopt(Product) == 2
    assert db.get(Product, "one") == first
    assert db.get(Product, "two") == second
    assert store.get(root.child(b"lots", b"s:L", b"quantity")) == b"d:2.50"
    assert db.query(Product).where(name="First").all(limit=5) == [first]
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "one")
    with pytest.raises(UniqueViolation):
        db.insert(Product(id="three", name="First"))


@pytest.mark.parametrize(
    "damage",
    ["extra_node", "missing_field", "bad_identity", "partial_metadata", "unique", "reference"],
)
def test_failed_adoption_preserves_all_data_and_metadata(store, damage):
    registry = Registry(Product)
    root = raw(store, registry, Product(id="one", name="First"))
    if damage == "extra_node":
        store.set(root.child(b"unknown", b"nested"), b"preserve")
    elif damage == "missing_field":
        store.delete_tree(root.child(b"name"))
    elif damage == "bad_identity":
        store.set(root.child(b"id"), b"s:wrong")
    elif damage == "partial_metadata":
        store.set(root.child(b"%m", b"layout"), b"1")
    elif damage == "unique":
        raw(store, registry, Product(id="two", name="First"))
    else:
        raw(
            store,
            registry,
            Product(id="two", name="Second", related=Reference[Product](key="absent")),
        )
    from mccann_hub.yhmf.adoption import _nodes

    before = _nodes(store, Path(Product.__global__))
    db = Database(store, registry)
    with pytest.raises((StorageError, UniqueViolation, ReferenceViolation)):
        db.adopt(Product)
    assert _nodes(store, Path(Product.__global__)) == before
    for global_name in ("^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
        assert store.children(Path(global_name), limit=1) == ()


def test_adoption_refuses_existing_metadata_and_unmanaged_index_namespaces(store):
    db = Database(store, Registry(Product))
    db.insert(Product(id="one", name="First"))
    with pytest.raises(StorageError):
        db.adopt(Product)
    store.delete_tree(Path("^YHMFSchema"))
    with pytest.raises(StorageError):
        db.adopt(Product)


def test_adoption_has_explicit_size_limits(store):
    registry = Registry(Product)
    for i in range(101):
        store.set(Path(Product.__global__, (f"s:{i}".encode(),)), b"raw")
    with pytest.raises(StorageError, match="at most 100"):
        Database(store, registry).adopt(Product)
    assert store.children(Path("^YHMFSchema"), limit=1) == ()
