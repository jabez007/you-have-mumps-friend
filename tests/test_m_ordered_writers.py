# SPDX-License-Identifier: GPL-3.0-or-later
"""M ordered-v1 keys preserve native collation, claims, and Python query results."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry
from tests import test_m_nullable_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_nullable_writers.m_writer
nullable_writer = test_m_nullable_writers.nullable_writer


class OrderedRow(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    amount: Decimal | None = Field(default=None, ordered=True, unique=True)
    text: str | None = Field(default=None, ordered=True)
    day: date = Field(ordered=True)
    instant: datetime = Field(ordered=True)
    rank: Literal[-1, 2] = Field(ordered=True)


def test_ordered_export_is_schema_bound_and_read_only(store):
    db = Database(store, Registry(OrderedRow))
    assert 'cfg("ordered","instant")="1"' in db.m_routine(OrderedRow, name="OrderedAPI")
    assert not db.inspect_schema(OrderedRow).installed


@pytest.fixture
def ordered_writer(store, nullable_writer, m_writer):
    db = Database(store, Registry(OrderedRow))
    _, run = nullable_writer
    _, _, routine, _ = m_writer
    replace_source(routine, db.m_routine(OrderedRow, name="YHMFTAPI"))
    return db, run


def values(amount="12.50", text="a\0\1雪", key="A"):
    return {
        "id": key,
        "amount": amount,
        "text": text,
        "day": "2024-02-29",
        "instant": "2024-02-29T00:00:00.000001Z",
        "rank": "2",
    }


def record(amount=Decimal("12.50"), text="a\0\1雪", key="A"):
    return OrderedRow(
        id=key,
        amount=amount,
        text=text,
        day=date(2024, 2, 29),
        instant=datetime(2024, 2, 29, 0, 0, 0, 1, tzinfo=UTC),
        rank=2,
    )


@pytest.mark.native
@pytest.mark.parametrize("text", [None, "", "a\0", "a\1", "雪😀"])
def test_all_ordered_key_families_match_python_nodes_and_queries(store, ordered_writer, text):
    db, run = ordered_writer
    assert run("insert", values(text=text)) == "ok"
    expected = Database(MemoryStore(), Registry(OrderedRow))
    expected.insert(record(text=text))
    roots = (OrderedRow.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected.store, roots)
    assert (
        db.query(OrderedRow).range("instant", ge=datetime(2024, 2, 29, tzinfo=UTC)).all()[0].id
        == "A"
    )
    assert db.query(OrderedRow).where(amount=Decimal("12.5000")).all()[0].id == "A"
    before = dump(store, roots)
    assert run("insert", values(amount="12.5000", text=text, key="B")) == "unique"
    assert dump(store, roots) == before
    assert run("replace", values(amount=None, text=text)) == "ok"
    expected.save(record(amount=None, text=text))
    assert run("insert", values(amount=None, text=text, key="B")) == "ok"
    expected.insert(record(amount=None, text=text, key="B"))
    assert dump(store, roots) == dump(expected.store, roots)
    assert len(db.query(OrderedRow).where(amount=None).all()) == 2
    assert db.check_indexes(OrderedRow).issues == ()
    assert run("delete", {"id": "A"}) == run("delete", {"id": "B"}) == "ok"
    assert db.check_indexes(OrderedRow).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "amount", ["1E+18", "1.123456789012345678", "1E-19", "9999999999999999999"]
)
def test_ordered_numeric_refusals_leave_no_schema_or_index_state(store, ordered_writer, amount):
    db, run = ordered_writer
    assert run("insert", values(amount=amount)) == "invalid"
    assert not db.inspect_schema(OrderedRow).installed
    assert dump(store, (OrderedRow.__global__, "^YHMFIndex", "^YHMFUnique")) == {}


@pytest.mark.native
@settings(
    max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(
    coefficient=st.integers(min_value=-(10**18 - 1), max_value=10**18 - 1),
    power=st.integers(min_value=-18, max_value=0),
)
def test_generated_m_ordered_decimal_keys_match_python(ordered_writer, coefficient, power):
    from mccann_hub.yhmf.ordered import value_key

    _, run = ordered_writer
    amount = Decimal((int(coefficient < 0), tuple(int(d) for d in str(abs(coefficient))), power))
    assert run("orderedkey", {"value": str(amount)}) == value_key(amount).decode()


@pytest.mark.native
def test_root_patch_updates_only_the_selected_ordered_field(store, ordered_writer):
    db, run = ordered_writer
    assert run("insert", values()) == "ok"
    expected = Database(MemoryStore(), Registry(OrderedRow))
    expected.insert(record())
    assert run("rootpatch", {"instant": "2025-01-01T00:00:00.000000Z"}) == "ok"
    expected.patch(OrderedRow, "A", changes={"instant": datetime(2025, 1, 1, tzinfo=UTC)})
    roots = (OrderedRow.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected.store, roots)
    assert db.check_indexes(OrderedRow).issues == ()


@pytest.mark.native
def test_owned_ordered_patch_reuses_normal_indexes_totals_and_references(store, m_writer):
    from examples.inventory import Balance, Bin, Location, Lot, Product, Warehouse
    from tests.test_m_owned_writers import namespaces, warehouse_values

    class OrderedBalance(Balance):
        quantity: Decimal = Field(ordered=True, ge=0, max_digits=18, decimal_places=6)

    class OrderedBin(Bin):
        balances: Children[OrderedBalance] = ChildField(key="id")

    class OrderedLocation(Location):
        bins: Children[OrderedBin] = ChildField(key="id")

    class Root(Warehouse):
        __schema_version__ = 4
        locations: Children[OrderedLocation] = ChildField(key="id")

    db = Database(store, Registry(Product, Lot, Root))
    expected = Database(MemoryStore(), Registry(Product, Lot, Root))
    for target in (db, expected):
        from mccann_hub.yhmf import Reference

        target.insert(Product(id="WIDGET", sku="SKU", name="Product"))
        target.insert(Lot(id="LOT-001", product=Reference[Product](key="WIDGET")))
        target.insert(Root.model_validate(warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})))
    _, run, routine, _ = m_writer
    source = db.m_routine(Root, name="YHMFTAPI")
    marker = " ; YHMF owned mutation complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    assert run("ownedpatch", {"quantity": "3.75"}) == "ok"
    expected.patch(
        Root,
        "MAIN",
        at=("locations", "DOCK", "bins", "B1", "balances", "LOT-001"),
        changes={"quantity": Decimal("3.75")},
    )
    assert dump(store, namespaces()) == dump(expected.store, namespaces())
    assert (
        db.query(Root).range("locations.bins.balances.quantity", ge=Decimal("3.5")).all()[0].id
        == "MAIN"
    )
    assert db.check_indexes(Root).issues == ()
