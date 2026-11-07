# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import create_model

from mccann_hub.yhmf import Database, Field, Model, Registry
from mccann_hub.yhmf.errors import QueryError
from tests.test_ordered_queries import Row


@pytest.mark.parametrize(
    "annotation,values",
    [
        (int, [-10, 0, 2, 10]),
        (Decimal, [Decimal("-2"), Decimal(".1"), Decimal(".20"), Decimal("10")]),
        (str, ["", "a\x00", "a\x01", "雪"]),
        (date, [date(2000, 1, 1), date(2020, 1, 1)]),
        (datetime, [datetime(2000, 1, 1, tzinfo=UTC), datetime(2020, 1, 1, tzinfo=UTC)]),
        (Literal["a", "b"], ["a", "b"]),
    ],
)
def test_descending_codecs_reverse_values_ties_nulls_and_occurrence_pages(
    store, annotation, values
):
    owner = create_model(
        "Owner",
        __base__=Model,
        id=(int, Field(primary_key=True)),
        value=(annotation | None, Field(default=None, ordered=True)),
    )
    owner.__global__ = "^YHMFTestProduct"
    db = Database(store, Registry(owner))
    for key, value in enumerate([*values, values[0], None]):
        db.insert(owner(id=key, value=value))
    forward = db.query(owner).order_by("value")
    backward = db.query(owner).order_by("value", direction="desc")
    assert [r.id for r in backward.all()] == [r.id for r in reversed(forward.all())]
    assert backward.all()[0].value is None
    assert backward.explain().order_direction == "desc"
    hits, cursor = [], None
    while page := backward.occurrences(limit=1, after=cursor):
        hits.extend(page)
        cursor = page[-1].cursor
    assert [(h.root_key, h.at) for h in hits] == [
        (h.root_key, h.at) for h in reversed(forward.occurrences())
    ]
    with pytest.raises(QueryError, match="cursor"):
        forward.occurrences(after=hits[0].cursor)


def test_descending_ranges_and_prefixes_keep_their_selection_and_budget(store):
    db = Database(store, Registry(Row))
    for key, value in enumerate([None, Decimal(-2), Decimal(0), Decimal(2), Decimal(10)]):
        db.insert(Row(id=key, amount=value, name="prefix" if key < 4 else "other"))
    for bounds in ({"ge": Decimal(0)}, {"le": Decimal(2)}, {"gt": Decimal(-2), "lt": Decimal(10)}):
        selection = db.query(Row).range("amount", **bounds)
        backward = selection.order_by("amount", direction="desc")
        assert [r.id for r in backward.all()] == [r.id for r in reversed(selection.all())]
        assert backward.explain().maximum_index_visits == 1000
    selection = db.query(Row).prefix("name", "pre")
    assert [h.root_key for h in selection.order_by("name", direction="desc").occurrences()] == [
        h.root_key for h in reversed(selection.occurrences())
    ]
    field = db.field(Row, lambda r: r.amount)
    assert db.query(Row).order_by(field, direction="desc").all()[0].id == 0
    with pytest.raises(QueryError, match="direction"):
        db.query(Row).order_by("amount", direction="sideways")


def test_descending_cursor_survives_deleted_value_group(store):
    db = Database(store, Registry(Row))
    db.insert(Row(id=1, amount=Decimal(1)))
    db.insert(Row(id=2, amount=Decimal(2)))
    selected = db.query(Row).order_by("amount", direction="desc")
    first = selected.occurrences(limit=1)[0]
    db.delete(Row, 2)
    assert [hit.root_key for hit in selected.occurrences(after=first.cursor)] == [1]
