# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
from decimal import Decimal

import pytest

from mccann_hub.yhmf import Database, Registry, each
from mccann_hub.yhmf.errors import QueryError, SchemaError, TransactionError
from tests.mutation_models import Product, Warehouse, warehouse
from tests.test_ordered_queries import Row
from tests.test_partial_mutations import ObservedStore


def test_typed_equalities_and_order_reuse_existing_plans(store):
    db = Database(store, Registry(Row))
    db.insert(Row(id=1, amount=Decimal(3), category="stock"))
    db.insert(Row(id=2, amount=Decimal(2), category="stock"))
    amount = db.field(Row, lambda r: r.amount)
    category = db.field(Row, lambda r: r.category)
    key = db.field(Row, lambda r: r.id)
    query = db.query(Row).where(category.eq("stock")).order_by(amount)
    previous = db.query(Row).where(category="stock").order_by("amount")
    assert query.explain() == previous.explain()
    assert [r.id for r in query.all()] == [2, 1]
    assert db.query(Row).where(key.eq(1)).all() == db.query(Row).where(id=1).all()
    assert db.query(Row).where(amount.eq(Decimal(2)), category="stock").all()[0].id == 2
    assert db.query(Row).where(amount.eq(None)).all() == []


def test_nested_query_fields_select_declarations_without_keys(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse())
    product = db.field(Warehouse, lambda w: each(each(w.bins).lots).product)
    note = db.field(Warehouse, lambda w: each(each(w.bins).lots).note)
    value = warehouse().bins["A"].lots["0"].product
    typed = db.query(Warehouse).where(product.eq(value))
    old = db.query(Warehouse).where(**{"bins.lots.product": value})
    assert typed.explain() == old.explain()
    assert typed.all() == old.all()
    assert typed.occurrences() == old.occurrences()
    # Selection does not imply an index or a scan.
    with pytest.raises(QueryError, match="no declared index"):
        db.query(Warehouse).where(note.eq("note"))


@pytest.mark.parametrize(
    "select",
    [
        lambda w: w.misspelled,
        lambda w: w,
        lambda w: w.bins,
        lambda w: w.bins["A"].id,
        lambda w: w.name if w.name else w.name,
        lambda w: "name",
    ],
)
def test_query_selectors_reject_non_declaration_access_without_io(store, select):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    with pytest.raises(SchemaError):
        db.field(Warehouse, select)
    assert observed.operations == []


def test_query_fields_check_root_schema_and_expired_handles(store):
    db = Database(store, Registry(Product, Warehouse))
    name = db.field(Product, lambda p: p.name)
    with pytest.raises(SchemaError, match="different model"):
        db.query(Warehouse).where(name.eq("x"))
    with pytest.raises(SchemaError, match="different model"):
        db.query(Warehouse).order_by(name)
    with pytest.raises(SchemaError, match="different model"):
        db.query(Product).where(replace(name, fingerprint=b"forged").eq("x"))
    expired = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError, match="expired"):
        expired.field(Product, lambda p: p.name)
    with pytest.raises(SchemaError, match="selector"):
        each({})
