# SPDX-License-Identifier: GPL-3.0-or-later
"""Initial installation and installed metadata must protect the entire global."""

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import StorageError


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str


@pytest.mark.parametrize(
    "raw",
    [
        Path(Product.__global__),
        Path(Product.__global__, (b"s:old",)),
        Path(Product.__global__, (b"s:new", b"raw")),
    ],
)
@pytest.mark.parametrize("method", ["insert", "save"])
def test_first_write_refuses_any_unmanaged_data(store, raw, method):
    store.set(raw, b"preserve")
    db = Database(store, Registry(Product))
    with pytest.raises(StorageError):
        getattr(db, method)(Product(id="new", name="New"))
    assert store.get(raw) == b"preserve"
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None
    assert store.children(Path("^YHMFSchema"), limit=1) == ()


@pytest.mark.parametrize("damage", ["missing_snapshot", "bad_snapshot", "missing_fingerprint"])
@pytest.mark.parametrize("operation", ["get", "save", "insert", "delete", "query", "scan"])
def test_crud_never_trusts_incomplete_or_changed_snapshot(store, damage, operation):
    db = Database(store, Registry(Product))
    db.insert(Product(id="old", name="Old"))
    schema = Path("^YHMFSchema", (b"YHMFTestProduct",))
    snapshot = store.get(schema.child(b"snapshot"))
    if damage == "missing_snapshot":
        store.delete_tree(schema.child(b"snapshot"))
    elif damage == "bad_snapshot":
        store.set(schema.child(b"snapshot"), b"{}")
    else:
        store.delete_tree(schema)
        store.set(schema.child(b"snapshot"), snapshot)
    operations = {
        "get": lambda: db.get(Product, "old"),
        "save": lambda: db.save(Product(id="old", name="Changed")),
        "insert": lambda: db.insert(Product(id="new", name="New")),
        "delete": lambda: db.delete(Product, "old"),
        "query": lambda: db.query(Product).where(id="old").all(limit=5),
        "scan": lambda: db.scan(Product, limit=5),
    }
    with pytest.raises(StorageError):
        operations[operation]()
    assert store.get(Path(Product.__global__, (b"s:old", b"name"))) == b"s:Old"
    assert store.get(Path(Product.__global__, (b"s:new", b"name"))) is None
