# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import (
    AlreadyExists,
    NotFound,
    SchemaError,
    SchemaMismatch,
    StorageError,
    TransactionError,
)


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    description: str
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None
    active: bool = False


@pytest.fixture
def db(store):
    return Database(store, Registry(Product))


def test_crud_is_explicit_and_preserves_types(db):
    original = Product(id="P42", description="Main", quantity=Decimal("12.50"))
    saved = db.insert(original)
    assert saved == original
    original.description = "Only in Python"
    assert db.get(Product, "P42").description == "Main"
    loaded = db.get(Product, "P42")
    assert str(loaded.quantity) == "12.50"
    assert loaded.note is None and loaded.active is False
    loaded.note = ""
    db.save(loaded)
    assert db.get(Product, "P42").note == ""
    assert db.delete(Product, "P42") is True
    assert db.delete(Product, "P42") is False
    with pytest.raises(NotFound):
        db.get(Product, "P42")


def test_insert_does_not_overwrite_and_save_is_upsert(db):
    db.save(Product(id="P42", description="first"))
    with pytest.raises(AlreadyExists):
        db.insert(Product(id="P42", description="second"))
    assert db.get(Product, "P42").description == "first"


def test_unvalidated_copy_cannot_bypass_constraints(db):
    original = Product(id="P42", description="Main")
    with pytest.raises(ValidationError):
        db.save(original.model_copy(update={"quantity": Decimal("-1")}))
    with pytest.raises(NotFound):
        db.get(Product, "P42")


def test_atomic_rollback_and_expired_handles(db):
    handles = []

    def abort(tx):
        handles.append(tx)
        tx.insert(Product(id="P42", description="Main"))
        raise RuntimeError("abort")

    with pytest.raises(RuntimeError, match="abort"):
        db.atomic(abort)
    with pytest.raises(NotFound):
        db.get(Product, "P42")
    with pytest.raises(TransactionError):
        handles[0].get(Product, "P42")
    assert db.atomic(lambda tx: tx.insert(Product(id="P42", description="Main"))).id == "P42"
    with pytest.raises(TransactionError):
        db.atomic(lambda tx: tx.atomic(lambda inner: None))


def test_golden_native_nodes(db):
    db.insert(Product(id="P42", description="Main", quantity=Decimal("12.50")))
    root = Path("^YHMFTestProduct", (b"s:P42",))
    assert db.store.get(root.child(b"description")) == b"s:Main"
    assert db.store.get(root.child(b"quantity")) == b"d:12.50"
    assert db.store.get(root.child(b"note")) == b"n:"
    assert db.store.get(root.child(b"active")) == b"b:0"
    assert db.store.get(root.child(b"%m", b"layout")) == b"1"


def test_schema_change_refuses_read_write_and_delete(db):
    db.insert(Product(id="P42", description="Main"))

    class Changed(Product):
        __schema_version__ = 2
        extra_field: int = 0

    changed = Database(db.store, Registry(Changed))
    for operation in (
        lambda: changed.get(Changed, "P42"),
        lambda: changed.save(Changed(id="P42", description="Different")),
        lambda: changed.delete(Changed, "P42"),
    ):
        with pytest.raises(SchemaMismatch):
            operation()
    assert db.get(Product, "P42").description == "Main"


def test_corrupt_record_is_not_silently_repaired(db):
    db.insert(Product(id="P42", description="Main"))
    root = Path("^YHMFTestProduct", (b"s:P42",))
    db.store.delete_tree(root.child(b"note"))
    with pytest.raises(StorageError):
        db.get(Product, "P42")


def test_raw_data_cannot_be_overwritten(db):
    root = Path("^YHMFTestProduct", (b"s:P42",))
    db.store.set(root.child(b"raw"), b"untouched")
    with pytest.raises(StorageError):
        db.save(Product(id="P42", description="Main"))
    assert db.store.get(root.child(b"raw")) == b"untouched"


def test_key_types_and_global_ownership(db):
    with pytest.raises(SchemaError):
        db.get(Product, 42)

    class Duplicate(Product):
        pass

    with pytest.raises(SchemaError):
        Registry(Product, Duplicate)


def test_registry_requires_a_primary_key():
    class NoKey(Model):
        __global__ = "^Test"
        name: str

    with pytest.raises(SchemaError):
        Registry(NoKey)


def test_future_index_flags_fail_explicitly():
    with pytest.raises(NotImplementedError):
        Field(unique=True)


@pytest.mark.parametrize("name", ["^YHMFSchema", "^YHMFIndex"])
def test_reject_framework_owned_globals(name):
    class Reserved(Product):
        __global__ = name

    with pytest.raises(SchemaError):
        Registry(Reserved)
