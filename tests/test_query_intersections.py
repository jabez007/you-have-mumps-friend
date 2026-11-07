# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date
from decimal import Decimal

import pytest

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    NativeStore,
    Path,
    Registry,
    index_scans,
    indexes,
)
from mccann_hub.yhmf.errors import QueryError, StorageError
from tests.test_ordered_queries import Row, database, ids
from tests.test_partial_mutations import ObservedStore


def populate(store):
    db = database(store)
    for key, category, value, name in [
        (1, "one", 5, "aa"),
        (2, "two", 10, "ab"),
        (3, "one", 20, "az"),
        (4, "one", 15, "ba"),
    ]:
        db.insert(
            Row(
                id=key,
                category=category,
                amount=Decimal(value),
                name=name,
                expires=date(2026, 1, key),
            )
        )
    return db


def test_chained_and_keyword_equalities_intersect_indexes(store):
    db = populate(store)
    assert ids(db.query(Row).where(category="one", name="az")) == [3]
    assert ids(db.query(Row).where(category="one").where(name="az")) == [3]
    assert ids(db.query(Row).where(category="one", name="ab")) == []
    assert ids(db.query(Row).where(id=3, name="az")) == [3]
    assert ids(db.query(Row).where(id=2, category="one")) == []
    assert ids(db.query(Row).where(id=99, category="one")) == []
    assert ids(db.query(Row).where(category="one", id=3)) == [3]


def test_predicates_preserve_immutable_query_reuse(store):
    db = populate(store)
    original = db.query(Row).where(category="one")
    selected = original.range("amount", ge=Decimal(10))
    assert ids(selected) == [3, 4]
    assert ids(original) == [1, 3, 4]
    assert ids(original.where(name="aa")) == [1]


def test_range_prefix_and_explicit_sort_select_the_candidate_index(store):
    db = populate(store)
    query = db.query(Row).range("amount", ge=Decimal(10)).where(category="one")
    assert ids(query) == [4, 3]
    assert ids(query.prefix("name", "a")) == [3]
    assert ids(db.query(Row).where(category="one").order_by("amount")) == [1, 4, 3]
    assert ids(db.query(Row).prefix("name", "a").range("amount", gt=Decimal(5))) == [2, 3]
    assert ids(
        db.query(Row)
        .where(category="one")
        .range("amount", ge=Decimal(10))
        .range("expires", le=date(2026, 1, 3))
    ) == [3]
    # Reusing the predicate's field for ordering is explicit and valid.
    assert ids(db.query(Row).range("amount", gt=Decimal(5)).order_by("amount")) == [2, 4, 3]


def test_equality_anchor_pages_over_nonmatching_candidates(store):
    db = populate(store)
    query = db.query(Row).where(category="one").range("amount", ge=Decimal(10))
    assert ids(query, limit=1) == [3]
    assert ids(query, limit=1, after=3) == [4]
    assert query.all(after=4) == []
    with pytest.raises(QueryError, match="cursor"):
        query.order_by("amount").all(after=3)
    with pytest.raises(QueryError, match="cursor"):
        db.query(Row).where(id=3, category="one").all(after=3)


def test_explain_lists_each_predicate_and_one_shared_bound(store):
    db = database(store)
    query = db.query(Row).range("amount", ge=Decimal(10)).where(category="secret")
    plan = query.explain()
    assert (plan.strategy, plan.field, plan.order_field) == (
        "index_intersection",
        "amount",
        "amount",
    )
    assert [
        (p.field, p.strategy, p.operation, p.maximum_index_visits) for p in plan.predicates
    ] == [
        ("amount", "ordered_index", "range", 1000),
        ("category", "equality_index", "equality", 1000),
    ]
    assert plan.maximum_index_visits == 1000
    assert "secret" not in repr(plan)
    ordered = db.query(Row).where(category="secret").order_by("name").explain()
    assert (ordered.field, ordered.order_field) == ("name", "name")
    assert len(ordered.predicates) == 1


def test_missing_indexes_and_ambiguous_projection_fail_clearly(store):
    db = database(store)
    with pytest.raises(QueryError, match="no scan"):
        db.query(Row).where(category="one", unknown="x")
    with pytest.raises(QueryError, match="ambiguous"):
        db.query(Row).where(category="one", name="aa").occurrences()
    with pytest.raises(QueryError, match="one predicate"):
        db.query(Row).range("amount", ge=Decimal(0)).where(amount=Decimal(1))
    with pytest.raises(QueryError, match="once"):
        db.query(Row).order_by("name").order_by("amount")
    with pytest.raises(QueryError, match="eight"):
        db.query(Row).where(**{f"p{i}": 1 for i in range(9)})


def test_conjunctions_bound_work_even_when_nothing_matches(store, monkeypatch):
    db = populate(store)
    original = index_scans.Budget
    monkeypatch.setattr(index_scans, "Budget", lambda: original(3))
    with pytest.raises(QueryError, match="budget"):
        db.query(Row).where(category="one", name="missing").all()
    assert ids(db.query(Row).where(id=1, category="one"), limit=1) == [1]


def test_owned_predicates_are_independent_existential_matches_under_root(store):
    class Item(Model):
        id: str = Field(primary_key=True)
        amount: int = Field(ordered=True)
        kind: str = Field(index=True)

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        items: Children[Item] = ChildField(key="id")

    db = Database(store, Registry(Root))
    db.insert(
        Root(
            id="A",
            items={"a": Item(id="a", amount=1, kind="x"), "b": Item(id="b", amount=2, kind="y")},
        )
    )
    selected = db.query(Root).where(**{"items.kind": "x"}).range("items.amount", ge=2)
    assert [r.id for r in selected.all()] == ["A"]
    assert [r.id for r in selected.order_by("items.amount").all()] == ["A"]
    with pytest.raises(QueryError, match="ambiguous"):
        selected.occurrences()


def test_intersections_seek_only_indexes_and_materialize_returned_roots(store):
    observed = ObservedStore(store)
    db = populate(observed)
    observed.operations.clear()
    assert ids(db.query(Row).where(category="two", name="ab")) == [2]
    assert not any(path == Path("^YHMFTestProduct") for _, path in observed.operations)
    assert not any(
        path.global_name == "^YHMFTestProduct" and path.subscripts[0] in (b"1", b"3", b"4")
        for _, path in observed.operations
    )


@pytest.mark.parametrize("damage", ["missing_record", "wrong_value", "claim"])
def test_each_returned_index_witness_is_validated(store, damage):
    class Unique(Row):
        category: str = Field(unique=True)

    db = Database(store, Registry(Unique))
    db.insert(Unique(id=1, category="one", amount=Decimal(1)))
    spec = db.registry.spec(Unique)
    if damage == "missing_record":
        store.delete_tree(spec.path(1))
    elif damage == "wrong_value":
        store.set(spec.path(1).child(b"category"), b"s:other")
    else:
        claim = next(
            e.claim_path
            for e in indexes.entries(spec, spec.path(1), db.get(Unique, 1))
            if e.claim_path is not None
        )
        store.delete_tree(claim)
    with pytest.raises(StorageError):
        db.query(Unique).where(id=1, category="one").all()
    with pytest.raises(StorageError):
        db.query(Unique).range("amount", ge=Decimal(0)).where(category="one").all()


@pytest.mark.native
def test_conjunctive_query_restarts_discard_prior_witnesses(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")
    db = populate(store)
    attempts = []

    def change(tx):
        attempts.append(ids(tx.query(Row).where(category="one").range("amount", ge=Decimal(10))))
        tx.patch(Row, 3, changes={"category": "two"})
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()
        return ids(tx.query(Row).where(category="one").range("amount", ge=Decimal(10)))

    assert db.atomic(change) == [4]
    assert attempts == [[3, 4], [3, 4]]
    assert db.check_indexes(Row).issues == ()


def test_generated_intersections_match_independent_python_predicates():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    from mccann_hub.yhmf import MemoryStore

    @settings(max_examples=25, deadline=None)
    @given(
        st.lists(
            st.tuples(st.integers(-20, 20), st.sampled_from(["one", "two"])),
            min_size=1,
            max_size=15,
        ),
        st.integers(-21, 21),
    )
    def check(values, threshold):
        db = database(MemoryStore())
        for key, (amount, category) in enumerate(values):
            db.insert(Row(id=key, amount=Decimal(amount), category=category))
        expected = [
            key
            for key, (amount, category) in enumerate(values)
            if amount >= threshold and category == "one"
        ]
        assert (
            ids(db.query(Row).where(category="one").range("amount", ge=Decimal(threshold)))
            == expected
        )
        sorted_expected = sorted(expected, key=lambda key: (values[key][0], key))
        assert (
            ids(db.query(Row).range("amount", ge=Decimal(threshold)).where(category="one"))
            == sorted_expected
        )

    check()
