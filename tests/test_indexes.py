# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal, localcontext

import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import NotFound, SchemaError, StorageError, UniqueViolation


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    category: str = Field(default="stock", index=True)
    price: Decimal = Field(default=Decimal("0"), index=True)


class Line(Model):
    code: str = Field(primary_key=True)
    serial: str = Field(unique=True)
    product: Reference[Product] = Field(index=True)


class Order(Model):
    __global__ = "^YHMFTestOrder"
    id: int = Field(primary_key=True)
    lines: Children[Line] = ChildField(key="code")


@pytest.fixture
def db(store):
    return Database(store, Registry(Product, Order))


def category_root(category="stock"):
    value = f'v:"s:{category}"'.encode()
    return Path("^YHMFIndex", (b"YHMFTestProduct", b"category", value))


def test_equality_index_is_maintained_on_insert_update_delete(db):
    db.insert(Product(id="P1", category="stock"))
    assert db.store.children(category_root(), limit=10) == (b"s:P1",)
    db.save(Product(id="P1", category="service"))
    assert db.store.children(category_root(), limit=10) == ()
    assert db.store.children(category_root("service"), limit=10) == (b"s:P1",)
    db.delete(Product, "P1")
    assert db.store.children(category_root("service"), limit=10) == ()


def test_unique_collision_cannot_leave_record_or_other_index_entries(db):
    db.insert(Product(id="P1", sku="SKU1"))
    with pytest.raises(UniqueViolation):
        db.insert(Product(id="P2", sku="SKU1"))
    with pytest.raises(NotFound):
        db.get(Product, "P2")
    assert db.store.children(category_root(), limit=10) == (b"s:P1",)


def test_unique_values_are_released_on_change_and_deletion(db):
    db.insert(Product(id="P1", sku="SKU1"))
    db.save(Product(id="P1", sku="SKU2"))
    db.insert(Product(id="P2", sku="SKU1"))
    db.delete(Product, "P2")
    db.insert(Product(id="P3", sku="SKU1"))
    db.save(db.get(Product, "P3"))


def test_null_does_not_claim_a_unique_value(db):
    db.insert(Product(id="P1"))
    db.insert(Product(id="P2"))
    null_root = Path("^YHMFIndex", (b"YHMFTestProduct", b"sku", b'v:"n:"'))
    assert db.store.children(null_root, limit=10) == (b"s:P1", b"s:P2")
    assert db.store.get(Path("^YHMFUnique", null_root.subscripts)) is None


def test_unique_strings_use_exact_case_sensitive_values(db):
    db.insert(Product(id="P1", sku="abc"))
    db.insert(Product(id="P2", sku="ABC"))
    with pytest.raises(UniqueViolation):
        db.save(Product(id="P2", sku="abc"))
    assert db.get(Product, "P2").sku == "ABC"


def test_decimal_index_equality_preserves_stored_precision(db):
    db.insert(Product(id="P1", price=Decimal("12.50")))
    db.insert(Product(id="P2", price=Decimal("12.500")))
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"price", b'v:"d:125E-1"'))
    assert db.store.children(root, limit=10) == (b"s:P1", b"s:P2")
    assert str(db.get(Product, "P1").price) == "12.50"
    assert str(db.get(Product, "P2").price) == "12.500"


def test_decimal_uniqueness_does_not_round_under_a_small_context(store):
    class Money(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        amount: Decimal = Field(unique=True)

    db = Database(store, Registry(Money))
    with localcontext() as context:
        context.prec = 4
        db.insert(Money(id="P1", amount=Decimal("123456789012345678901234567890.10")))
        db.insert(Money(id="P2", amount=Decimal("123456789012345678901234567890.11")))
        with pytest.raises(UniqueViolation):
            db.insert(Money(id="P3", amount=Decimal("123456789012345678901234567890.100")))


def test_unique_child_fields_are_scoped_to_the_declaration_path(db):
    db.insert(Product(id="P1"))
    ref = Reference[Product](key="P1")
    db.insert(Order(id=1, lines={"A": Line(code="A", serial="S1", product=ref)}))
    with pytest.raises(UniqueViolation):
        db.insert(Order(id=2, lines={"B": Line(code="B", serial="S1", product=ref)}))
    assert db.store.get(Path("^YHMFRef", (b"YHMFTestProduct", b"s:P1"))) == b"1"
    with pytest.raises(NotFound):
        db.get(Order, 2)


def test_duplicates_within_one_child_collection_are_rejected(db):
    db.insert(Product(id="P1"))
    ref = Reference[Product](key="P1")
    with pytest.raises(UniqueViolation):
        db.insert(
            Order(
                id=1,
                lines={
                    "A": Line(code="A", serial="S1", product=ref),
                    "B": Line(code="B", serial="S1", product=ref),
                },
            )
        )
    assert db.store.get(Path("^YHMFRef", (b"YHMFTestProduct", b"s:P1"))) is None


def test_index_entries_and_claims_roll_back_together(db):
    def fail(tx):
        tx.insert(Product(id="P1", sku="SKU1"))
        raise RuntimeError("abort indexed write")

    with pytest.raises(RuntimeError, match="abort indexed"):
        db.atomic(fail)
    assert db.store.children(category_root(), limit=10) == ()
    db.insert(Product(id="P2", sku="SKU1"))


def test_corrupt_index_cannot_be_silently_overwritten(db):
    db.insert(Product(id="P1", sku="SKU1"))
    db.store.delete_tree(category_root())
    with pytest.raises(StorageError, match="secondary index"):
        db.save(Product(id="P1", sku="SKU2"))
    assert db.get(Product, "P1").sku == "SKU1"


def test_native_index_key_limits_are_checked_before_writes(db):
    with pytest.raises(StorageError, match="key-size"):
        db.insert(Product(id="P1", category="x" * 600))
    with pytest.raises(NotFound):
        db.get(Product, "P1")


def test_containers_cannot_be_indexed():
    class Embedded(Model):
        value: str

    class BadEmbedded(Model):
        __global__ = "^Invalid"
        id: int = Field(primary_key=True)
        value: Embedded = Field(index=True)

    with pytest.raises(SchemaError, match="scalar or Reference"):
        Registry(BadEmbedded)


@pytest.mark.parametrize("flag", ["index", "unique"])
def test_child_field_cannot_silently_ignore_index_flags(flag):
    with pytest.raises(SchemaError, match="scalar or Reference"):
        ChildField(key="code", **{flag: True})
