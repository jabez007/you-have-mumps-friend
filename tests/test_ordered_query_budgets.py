# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Registry
from mccann_hub.yhmf.errors import QueryError


class Value(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    value: int = Field(ordered=True)


class Item(Model):
    id: str = Field(primary_key=True)
    value: int = Field(ordered=True)


class Owner(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    category: str = Field(index=True)
    items: Children[Item] = ChildField(key="id")


def test_ordered_all_result_ceiling_does_not_increase_the_work_budget(store):
    db = Database(store, Registry(Value))
    for offset in range(0, 700, 50):

        def insert_batch(tx, start=offset):
            for key in range(start, start + 50):
                tx.insert(Value(id=key, value=key))

        db.atomic(insert_batch)
    query = db.query(Value).order_by("value")
    assert len(query.all(limit=500)) == 500
    for limit in (600, 1000):
        with pytest.raises(QueryError, match=rf"{limit}-record.*result ceiling.*page\(\)"):
            query.all(limit=limit)
    plan = query.explain()
    assert plan.maximum_limit == plan.maximum_index_visits == 1000
    first = query.page(limit=1000)
    assert len(first.records) == 500 and first.cursor is not None
    second = query.page(limit=1000, after=first.cursor)
    assert [row.id for row in (*first.records, *second.records)] == list(range(700))
    assert second.cursor is None


def test_filtered_ordered_all_can_exhaust_budget_before_one_result_but_pages_progress(store):
    db = Database(store, Registry(Owner))
    db.insert(
        Owner(
            id="A", category="ignore", items={str(i): Item(id=str(i), value=0) for i in range(1000)}
        )
    )
    db.insert(Owner(id="B", category="wanted", items={"one": Item(id="one", value=1)}))
    query = db.query(Owner).where(category="wanted").order_by("items.value")
    with pytest.raises(QueryError, match=r"1-record.*result ceiling.*page\(\)"):
        query.all(limit=1)
    first = query.page(limit=1)
    assert first.records == () and first.cursor is not None
    second = query.page(limit=1, after=first.cursor)
    assert [row.id for row in second.records] == ["B"]
