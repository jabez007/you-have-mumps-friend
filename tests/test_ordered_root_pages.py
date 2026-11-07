# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Registry, index_scans
from mccann_hub.yhmf.errors import QueryError, StorageError
from tests.test_ordered_queries import Row


class Item(Model):
    id: str = Field(primary_key=True)
    amount: int | None = Field(default=None, ordered=True)


class Root(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    category: str = Field(default="stock", index=True)
    items: Children[Item] = ChildField(key="id")


def seeded(store):
    db = Database(store, Registry(Root))
    for key, values in (("A", [1, 100, 100]), ("B", [2, 2]), ("C", [3]), ("N", [None])):
        db.insert(
            Root(id=key, items={str(i): Item(id=str(i), amount=v) for i, v in enumerate(values)})
        )
    return db


def collect(query, limit=1):
    result, cursor = [], None
    for _ in range(100):
        page = query.page(limit=limit, after=cursor)
        result.extend(record.id for record in page.records)
        if page.cursor is None:
            return result
        assert page.cursor != cursor
        cursor = page.cursor
    raise AssertionError("Root cursor did not make bounded progress")


@pytest.mark.parametrize(
    "direction,expected", [("asc", ["A", "B", "C", "N"]), ("desc", ["N", "A", "C", "B"])]
)
def test_owned_root_pages_choose_one_canonical_occurrence_across_every_page(
    store, direction, expected
):
    db = seeded(store)
    query = db.query(Root).order_by("items.amount", direction=direction)
    assert collect(query) == expected
    assert collect(query, 2) == expected
    first = query.page(limit=1)
    assert [r.id for r in query.all(after=first.cursor)] == expected[1:]
    assert first.records[0].id not in collect_after(query, first.cursor)


def collect_after(query, cursor):
    result = []
    while True:
        page = query.page(limit=1, after=cursor)
        result.extend(r.id for r in page.records)
        if page.cursor is None:
            return result
        cursor = page.cursor


def test_root_cursors_bind_query_predicates_direction_and_projection(store):
    db = seeded(store)
    query = db.query(Root).where(category="stock").order_by("items.amount")
    first = query.page(limit=1)
    assert collect_after(query, first.cursor) == ["B", "C", "N"]
    alternatives = [
        db.query(Root).order_by("items.amount"),
        db.query(Root).where(category="other").order_by("items.amount"),
        db.query(Root).where(category="stock").order_by("items.amount", direction="desc"),
    ]
    for other in alternatives:
        with pytest.raises(QueryError, match="cursor"):
            other.page(after=first.cursor)
    plain = db.query(Root).order_by("items.amount")
    with pytest.raises(QueryError, match="cursor"):
        plain.page(after=plain.occurrences(limit=1)[0].cursor)
    with pytest.raises(QueryError, match="cursor"):
        plain.occurrences(after=plain.page(limit=1).cursor)
    with pytest.raises(QueryError, match="ordered"):
        db.query(Root).where(category="stock").page()


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_root_scalar_and_range_pages_continue_after_deleted_position(store, direction):
    db = Database(store, Registry(Row))
    for key in range(4):
        db.insert(Row(id=key, amount=Decimal(key)))
    query = db.query(Row).range("amount", ge=Decimal(0)).order_by("amount", direction=direction)
    expected = [r.id for r in query.all()]
    page = query.page(limit=1)
    db.delete(Row, page.records[0].id)
    assert collect_after(query, page.cursor) == expected[1:]


def test_budget_exhaustion_returns_safe_progress_pages_without_dropping_roots(store, monkeypatch):
    db = seeded(store)
    original = index_scans.Budget
    monkeypatch.setattr(index_scans, "Budget", lambda: original(3))
    query = db.query(Root).order_by("items.amount")
    assert collect(query) == ["A", "B", "C", "N"]
    with pytest.raises(QueryError, match="budget"):
        query.all()
    # A rejected predicate may produce no roots but must still advance its cursor.
    # Four visits cover an exhausted cursor group plus a new group/entry/witness.
    monkeypatch.setattr(index_scans, "Budget", lambda: original(4))
    filtered = db.query(Root).where(category="other").order_by("items.amount")
    page = filtered.page()
    assert page.records == () and page.cursor is not None
    assert collect_after(filtered, page.cursor) == []


def test_missing_canonical_occurrence_fails_instead_of_hiding_a_root(store):
    db = seeded(store)
    hit = db.query(Root).order_by("items.amount").occurrences(limit=1)[0]
    spec = db.registry.spec(Root)
    from mccann_hub.yhmf import indexes
    from mccann_hub.yhmf.records import walk_values

    entry = next(
        e
        for e in indexes.entries_at(
            spec, spec.path("A"), walk_values(spec.fields, db.get(Root, "A"))
        )
        if e.path.subscripts[-3] == b"1"
    )
    assert hit.root_key == "A"
    store.delete_tree(entry.path)
    with pytest.raises(StorageError, match="Canonical"):
        db.query(Root).order_by("items.amount").page()


def test_forward_queries_keep_runtime_support_for_existing_store_children_signatures(store):
    class ExistingStore:
        def __getattr__(self, name):
            return getattr(store, name)

        def children(self, path, *, limit, after=b""):
            return store.children(path, limit=limit, after=after)

    db = Database(ExistingStore(), Registry(Row))
    db.insert(Row(id=1, amount=Decimal(1)))
    assert db.query(Row).order_by("amount").all()[0].id == 1
