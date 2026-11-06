# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import NotFound, StorageError
from tests.adversarial_support import dump


class Item(Model):
    id: int = Field(primary_key=True)
    value: str = ""


class Parent(Model):
    __global__ = "^YHMFTestWarehouse"
    id: int = Field(primary_key=True)
    items: Children[Item] | None = ChildField(key="id", default=None)


def apply(db, operation):
    if operation == "get":
        return db.get(Parent, 1, at=("items", 1))
    if operation == "patch":
        return db.patch(Parent, 1, at=("items", 1), changes={"value": "new"})
    if operation == "insert":
        return db.insert_child(Parent, 1, collection=("items",), record=Item(id=1))
    return db.delete_child(Parent, 1, at=("items", 1))


@pytest.mark.parametrize("operation", ["get", "patch", "insert", "delete"])
def test_null_collection_is_an_absent_owner_without_any_writes(store, operation):
    db = Database(store, Registry(Parent))
    db.insert(Parent(id=1))
    roots = (Parent.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFRef")
    before = dump(store, roots)
    with pytest.raises(NotFound):
        apply(db, operation)
    assert dump(store, roots) == before
    assert db.get(Parent, 1).items is None


@pytest.mark.parametrize("marker", [None, b"c:-1", b"c:01", b"c:1001", b"broken", b"o:1"])
def test_malformed_or_missing_collection_markers_remain_corruption(store, marker):
    db = Database(store, Registry(Parent))
    db.insert(Parent(id=1))
    path = Path(Parent.__global__, (b"1", b"items"))
    if marker is None:
        store.delete_tree(path)
    else:
        store.set(path, marker)
    before = dump(store, (Parent.__global__, "^YHMFSchema"))
    with pytest.raises(StorageError):
        apply(db, "insert")
    assert dump(store, (Parent.__global__, "^YHMFSchema")) == before


@pytest.mark.parametrize("operation", ["get", "patch", "insert", "delete"])
def test_null_collection_with_descendants_remains_corruption(store, operation):
    db = Database(store, Registry(Parent))
    db.insert(Parent(id=1))
    store.set(Path(Parent.__global__, (b"1", b"items", b"1")), b"o:1")
    with pytest.raises(StorageError):
        apply(db, operation)
