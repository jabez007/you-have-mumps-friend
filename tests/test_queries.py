# SPDX-License-Identifier: GPL-3.0-or-later
import json
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import QueryError, StorageError, TransactionError


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    category: str = Field(default="stock", index=True)
    price: Decimal = Field(default=Decimal("0"), index=True)
    sku: str | None = Field(default=None, unique=True)
    name: str = "Unnamed"


class Line(Model):
    code: str = Field(primary_key=True)
    product: Reference[Product] = Field(index=True)


class Order(Model):
    __global__ = "^YHMFTestOrder"
    id: int = Field(primary_key=True)
    lines: Children[Line] = ChildField(key="code")


@pytest.fixture
def db(store):
    return Database(store, Registry(Product, Order))


def test_index_queries_are_bounded_and_use_native_owner_key_order(db):
    for key in (10, 2, 1):
        db.insert(Product(id=key))
    query = db.query(Product).where(category="stock")
    assert [product.id for product in query.all(limit=2)] == [1, 2]
    assert [product.id for product in query.all(limit=2, after=2)] == [10]
    assert query.all(after=10) == []


def test_direct_key_queries_work_without_an_index(db):
    db.insert(Product(id=10))
    query = db.query(Product).where(id=10)
    assert query.explain().strategy == "direct_key"
    assert [product.id for product in query.all()] == [10]
    assert db.query(Product).where(id=20).all() == []
    with pytest.raises(QueryError, match="cursors"):
        query.all(after=1)


def test_query_explanation_does_not_include_sensitive_filter_values(db):
    plan = db.query(Product).where(sku="secret-SKU").explain()
    assert plan.strategy == "equality_index"
    assert plan.global_name == "^YHMFTestProduct"
    assert plan.field == "sku"
    assert plan.maximum_limit == 1000
    assert "secret-SKU" not in repr(plan)


def test_missing_indexes_never_trigger_a_fallback_scan(db):
    db.insert(Product(id=1, name="Main"))
    with pytest.raises(QueryError, match="no scan"):
        db.query(Product).where(name="Main")
    with pytest.raises(QueryError, match="no scan"):
        db.query(Product).where(unknown="Main")


def test_explicit_scan_is_bounded_and_paginated(db):
    for key in (10, 2, 1):
        db.insert(Product(id=key))
    assert [product.id for product in db.scan(Product, limit=2)] == [1, 2]
    assert [product.id for product in db.scan(Product, limit=2, after=2)] == [10]


def test_query_requires_at_least_one_predicate_and_rejects_repeated_fields(db):
    with pytest.raises(QueryError):
        db.query(Product).all()
    with pytest.raises(QueryError):
        db.query(Product).where()
    with pytest.raises(QueryError):
        db.query(Product).where(category="stock").where(category="other")
    with pytest.raises(QueryError):
        db.query(Product).where(category="stock").range("category", ge="a")


@pytest.mark.parametrize("limit", [0, -1, 1001, True])
def test_query_and_scan_limits_are_enforced(db, limit):
    with pytest.raises(QueryError, match="limits"):
        db.query(Product).where(category="stock").all(limit=limit)
    with pytest.raises(QueryError, match="limits"):
        db.scan(Product, limit=limit)


def test_index_queries_validate_declared_value_types(db):
    with pytest.raises(ValidationError):
        db.query(Product).where(price="12.50")
    with pytest.raises(ValidationError):
        db.query(Product).where(category=42)


def test_direct_key_queries_validate_declared_key_constraints(store):
    class Positive(Model):
        __global__ = "^YHMFTestProduct"
        id: int = Field(primary_key=True, gt=0)

    db = Database(store, Registry(Positive))
    with pytest.raises(ValidationError):
        db.query(Positive).where(id=-1)


def test_decimal_queries_share_numeric_equality(db):
    db.insert(Product(id=1, price=Decimal("12.50")))
    db.insert(Product(id=2, price=Decimal("12.500")))
    found = db.query(Product).where(price=Decimal("12.5")).all()
    assert [product.id for product in found] == [1, 2]
    assert [str(product.price) for product in found] == ["12.50", "12.500"]


def test_null_values_remain_queryable_in_unique_fields(db):
    db.insert(Product(id=1))
    db.insert(Product(id=2))
    assert [product.id for product in db.query(Product).where(sku=None).all()] == [1, 2]


def test_owned_reference_queries_return_each_owning_root_once(db):
    db.insert(Product(id=1))
    ref = Reference[Product](key=1)
    db.insert(
        Order(
            id=1,
            lines={
                "A": Line(code="A", product=ref),
                "B": Line(code="B", product=ref),
            },
        )
    )
    query = db.query(Order).where(**{"lines.product": ref})
    assert [order.id for order in query.all(limit=1)] == [1]
    assert query.all(after=1) == []


def test_transaction_bound_queries_expire_with_their_handle(db):
    query = db.atomic(lambda tx: tx.query(Product).where(category="stock"))
    with pytest.raises(TransactionError):
        query.all()
    with pytest.raises(TransactionError):
        query.explain()


def test_stale_index_matches_are_reported_as_corruption(db):
    db.insert(Product(id=1))
    db.store.set(Path("^YHMFTestProduct", (b"1", b"category")), b"s:changed")
    with pytest.raises(StorageError, match="does not match"):
        db.query(Product).where(category="stock").all()


def test_orphan_index_entries_are_not_silently_skipped(db):
    db.insert(Product(id=1))
    db.store.delete_tree(Path("^YHMFTestProduct", (b"1",)))
    with pytest.raises(StorageError, match="missing record"):
        db.query(Product).where(category="stock").all()


def test_index_audit_is_bounded_and_reports_missing_claims(db):
    db.insert(Product(id=1, sku="secret-SKU"))
    db.insert(Product(id=2, sku="SKU2"))
    assert db.check_indexes(Product).issues == ()
    claim = Path("^YHMFUnique", (b"YHMFTestProduct", b"sku", b'v:"s:secret-SKU"'))
    db.store.delete_tree(claim)
    report = db.check_indexes(Product, limit=1)
    assert report.checked_records == 1 and report.next_key == 1
    assert [(issue.field, issue.kind) for issue in report.issues] == [
        ("sku", "missing_or_corrupt_claim"),
    ]
    assert "secret-SKU" not in repr(report)
    assert db.check_indexes(Product, after=1).issues == ()


_VALUES = json.loads((FilePath(__file__).parent / "fixtures/index-values.json").read_text())


@pytest.mark.parametrize("case", _VALUES)
def test_golden_index_value_contract(store, case):
    class Values(Model):
        __global__ = "^YHMFTestProduct"
        id: int = Field(primary_key=True)
        amount: Decimal | None = Field(default=None, index=True)
        number: float | None = Field(default=None, index=True)
        text: str | None = Field(default=None, index=True)
        binary: bytes | None = Field(default=None, index=True)

    value = case["value"]
    converters = {"amount": Decimal, "binary": bytes.fromhex}
    if value is not None and case["field"] in converters:
        value = converters[case["field"]](value)
    db = Database(store, Registry(Values))
    db.insert(Values(id=1, **{case["field"]: value}))
    root = Path(
        "^YHMFIndex",
        (b"YHMFTestProduct", case["field"].encode("ascii"), case["token"].encode("ascii")),
    )
    assert db.store.children(root, limit=10) == (b"1",)
    assert [record.id for record in db.query(Values).where(**{case["field"]: value}).all()] == [1]
