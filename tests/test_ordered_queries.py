# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    Path,
    Registry,
    index_scans,
    indexes,
)
from mccann_hub.yhmf.errors import QueryError, StorageError
from tests.test_partial_mutations import ObservedStore


class Row(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    amount: Decimal | None = Field(default=None, ordered=True)
    expires: date | None = Field(default=None, ordered=True)
    name: str = Field(default="item", ordered=True, min_length=2)
    category: str = Field(default="stock", index=True)


def database(store):
    return Database(store, Registry(Row))


def ids(query, **kwargs):
    return [row.id for row in query.all(**kwargs)]


@pytest.mark.parametrize(
    "bounds,expected",
    [
        ({"ge": Decimal("-.5"), "le": Decimal("2")}, [2, 3, 4]),
        ({"gt": Decimal("-.5"), "lt": Decimal("2")}, [3]),
        ({"lt": Decimal("0")}, [1, 2]),
        ({"ge": Decimal("2")}, [4, 5]),
        ({"ge": Decimal(".1"), "le": Decimal(".2")}, []),
        ({"gt": Decimal("2"), "le": Decimal("2")}, []),
        ({"le": Decimal("-100")}, []),
    ],
)
def test_numeric_ranges_seek_native_value_order_and_exclude_null(store, bounds, expected):
    db = database(store)
    for key, value in enumerate(
        [Decimal(-10), Decimal("-.5"), Decimal(0), Decimal(2), Decimal(10), None], 1
    ):
        db.insert(Row(id=key, amount=value))
    assert ids(db.query(Row).range("amount", **bounds)) == expected
    assert ids(db.query(Row).order_by("amount")) == [1, 2, 3, 4, 5, 6]


def test_date_ranges_and_equal_values_have_deterministic_ties(store):
    db = database(store)
    for key, day in [(10, 3), (2, 2), (1, 2), (20, 1)]:
        db.insert(Row(id=key, expires=date(2026, 1, day)))
    selected = db.query(Row).range("expires", ge=date(2026, 1, 2), lt=date(2026, 1, 4))
    assert ids(selected) == [1, 2, 10]
    page, cursor, result = [], None, []
    for _ in range(5):
        page = selected.occurrences(limit=1, after=cursor)
        if not page:
            break
        result.extend(hit.root_key for hit in page)
        cursor = page[-1].cursor
    assert result == [1, 2, 10]


@pytest.mark.parametrize(
    "prefix,expected",
    [("a", [1, 2, 3]), ("a\0", [1]), ("雪", [4]), ("", [1, 2, 3, 5, 4]), ("ac", []), ("\x01", [])],
)
def test_literal_prefix_is_not_a_complete_validated_field_value(store, prefix, expected):
    db = database(store)
    for key, text in enumerate(["a\0", "a\x01", "ab", "雪人", "ba"], 1):
        db.insert(Row(id=key, name=text))
    assert ids(db.query(Row).prefix("name", prefix)) == expected


def test_prefix_does_not_apply_model_normalization(store):
    from pydantic import ConfigDict

    class Normalized(Row):
        model_config = ConfigDict(str_to_lower=True)

    db = Database(store, Registry(Normalized))
    db.insert(Normalized(id=1, name="ABC"))
    assert db.query(Normalized).prefix("name", "A").all() == []
    assert [hit.root_key for hit in db.query(Normalized).prefix("name", "a").occurrences()] == [1]


def test_owned_ordered_occurrences_page_without_loading_unrelated_siblings(store):
    class Item(Model):
        id: str = Field(primary_key=True)
        value: Decimal = Field(ordered=True)

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        items: Children[Item] = ChildField(key="id")

    observed = ObservedStore(store)
    db = Database(observed, Registry(Root))
    db.insert(Root(id="A", items={str(i): Item(id=str(i), value=Decimal(i)) for i in range(1000)}))
    observed.operations.clear()
    selected = db.query(Root).range("items.value", ge=Decimal(998))
    hits = selected.occurrences(limit=1)
    assert [(h.root_key, h.at, h.record.value) for h in hits] == [
        ("A", ("items", "998"), Decimal(998))
    ]
    assert not any(b"s:0" in path.subscripts for _, path in observed.operations)
    assert len(observed.operations) < 100
    assert [h.at for h in selected.occurrences(after=hits[0].cursor)] == [("items", "999")]
    assert [r.id for r in selected.all()] == ["A"]


def test_cursor_can_continue_after_owner_and_value_group_disappear(store):
    db = database(store)
    db.insert(Row(id=1, amount=Decimal(1)))
    db.insert(Row(id=2, amount=Decimal(2)))
    selected = db.query(Row).order_by("amount")
    first = selected.occurrences(limit=1)[0]
    db.delete(Row, 1)
    assert [h.root_key for h in selected.occurrences(after=first.cursor)] == [2]
    with pytest.raises(QueryError, match="cursor"):
        selected.all(after=1)
    with pytest.raises(QueryError, match="cursor"):
        db.query(Row).range("amount", ge=Decimal(0)).occurrences(after=first.cursor)
    with pytest.raises(QueryError, match="cursor"):
        db.query(Row).order_by("name").occurrences(after=first.cursor)


@pytest.mark.parametrize("token", ["", "not-base64", "e30=", 1, "x" * 4097])
def test_invalid_ordered_cursors_fail_clearly(store, token):
    with pytest.raises(QueryError, match="cursor"):
        database(store).query(Row).order_by("amount").occurrences(after=token)


def test_cursor_is_scoped_to_installed_schema(store):
    db = database(store)
    db.insert(Row(id=1))
    cursor = db.query(Row).order_by("amount").occurrences()[0].cursor

    class Revised(Row):
        __schema_version__ = 2

    revised = Database(store, Registry(Revised))
    # Cursor checks are also independently scoped, before an application migration.
    selection = index_scans.Selection(
        indexes.definitions(revised.registry.spec(Revised))[0], "ordered"
    )
    with pytest.raises(QueryError, match="cursor"):
        index_scans.decode_cursor(revised.registry.spec(Revised), selection, cursor)


def test_index_walk_budget_is_explicit_and_never_returns_an_incomplete_success(store, monkeypatch):
    db = database(store)
    for i in range(4):
        db.insert(Row(id=i, amount=Decimal(i)))
    original = index_scans.Budget
    monkeypatch.setattr(index_scans, "Budget", lambda: original(3))
    with pytest.raises(QueryError, match="budget"):
        db.query(Row).order_by("amount").all()
    assert ids(db.query(Row).order_by("amount"), limit=1) == [0]
    page = db.query(Row).order_by("amount").occurrences(limit=1)
    assert [
        h.root_key
        for h in db.query(Row).order_by("amount").occurrences(limit=1, after=page[0].cursor)
    ] == [1]


def test_explain_has_index_bound_without_sensitive_values(store):
    selected = database(store).query(Row).prefix("name", "sensitive")
    plan = selected.explain()
    assert (plan.strategy, plan.field, plan.maximum_index_visits) == ("ordered_index", "name", 1000)
    assert "sensitive" not in repr(plan)


def test_query_declarations_reject_unindexed_and_invalid_ranges(store):
    db = database(store)
    with pytest.raises(QueryError, match="ordered index"):
        db.query(Row).range("category", ge="stock")
    for bounds in [
        {},
        {"gt": 1, "ge": 2},
        {"lt": 1, "le": 2},
        {"ge": Decimal(2), "le": Decimal(1)},
    ]:
        with pytest.raises(QueryError):
            db.query(Row).range("amount", **bounds)
    with pytest.raises(QueryError, match="string"):
        db.query(Row).prefix("amount", "1")
    with pytest.raises(QueryError, match="string"):
        db.query(Row).prefix("name", 1)
    with pytest.raises(ValidationError):
        db.query(Row).range("amount", ge="1")


@pytest.mark.parametrize("damage", ["value", "location", "claim", "namespace", "noncanonical"])
def test_ordered_walks_validate_concrete_entries_and_unique_claims(store, damage):
    class Unique(Row):
        name: str = Field(ordered=True, unique=True)

    db = Database(store, Registry(Unique))
    db.insert(Unique(id=1, name="AA"))
    spec = db.registry.spec(Unique)
    entry = next(
        e
        for e in indexes.entries(spec, spec.path(1), db.get(Unique, 1))
        if e.claim_path is not None
    )
    if damage == "value":
        store.set(spec.path(1).child(b"name"), b"s:BB")
    elif damage == "location":
        store.set(entry.path, b"[]")
    elif damage == "claim":
        store.delete_tree(entry.claim_path)
    elif damage == "namespace":
        store.set(Path("^YHMFIndex", (b"YHMFTestProduct", b"name")), b"bad")
    else:
        store.set(
            Path("^YHMFIndex", (b"YHMFTestProduct", b"name", b"nonsense", b"1", b"a" * 64)), b"[]"
        )
    with pytest.raises(StorageError):
        db.query(Unique).order_by("name").occurrences()


def test_ordered_choices_and_utc_datetimes_use_declared_types(store):
    class Kind(Enum):
        EARLY = "a"
        LATE = "z"

    class Typed(Model):
        __global__ = "^YHMFTestProduct"
        id: int = Field(primary_key=True)
        kind: Kind = Field(ordered=True)
        when: datetime = Field(ordered=True)

    db = Database(store, Registry(Typed))
    db.insert(Typed(id=1, kind=Kind.LATE, when=datetime(2026, 1, 2, tzinfo=UTC)))
    db.insert(Typed(id=2, kind=Kind.EARLY, when=datetime(2026, 1, 1, tzinfo=UTC)))
    assert [r.id for r in db.query(Typed).range("kind", ge=Kind.EARLY).all()] == [2, 1]
    assert [h.root_key for h in db.query(Typed).order_by("when").occurrences()] == [2, 1]


def test_generated_numeric_range_results_match_an_independent_sorted_reference():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    from mccann_hub.yhmf import MemoryStore

    @settings(max_examples=30, deadline=None)
    @given(
        st.lists(st.integers(-1000, 1000), min_size=1, max_size=30),
        st.integers(-1001, 1001),
        st.integers(-1001, 1001),
    )
    def check(values, left, right):
        db = database(MemoryStore())
        for i, value in enumerate(values):
            db.insert(Row(id=i, amount=Decimal(value)))
        low, high = sorted((left, right))
        expected = [
            i
            for i in sorted(range(len(values)), key=lambda i: (values[i], i))
            if low <= values[i] < high
        ]
        assert ids(db.query(Row).range("amount", ge=Decimal(low), lt=Decimal(high))) == expected

    check()


@pytest.mark.native
def test_range_read_results_are_recomputed_after_native_restart(store):
    from mccann_hub.yhmf import NativeStore

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")
    db = database(store)
    db.insert(Row(id=1, amount=Decimal(1)))
    attempts = []

    def change(tx):
        attempts.append(ids(tx.query(Row).range("amount", le=Decimal(1))))
        tx.patch(Row, 1, changes={"amount": Decimal(2)})
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()
        return ids(tx.query(Row).range("amount", ge=Decimal(2)))

    assert db.atomic(change) == [1]
    assert attempts == [[1], [1]]
    assert db.check_indexes(Row).issues == ()


@pytest.mark.parametrize("component", [0, 1, 2, 3, 4])
def test_ordered_cursor_rejects_nonstring_position_components(store, component):
    import base64
    import json

    db = database(store)
    db.insert(Row(id=1, amount=Decimal(1)))
    selected = db.query(Row).order_by("amount")
    first = selected.occurrences()[0]
    data = json.loads(base64.urlsafe_b64decode(first.cursor))
    data[component] = 1 if component != 0 else True
    token = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    with pytest.raises(QueryError):
        selected.occurrences(after=token)
