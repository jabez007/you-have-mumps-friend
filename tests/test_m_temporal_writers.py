# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical calendar and UTC M inputs agree with the existing temporal codec."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_writers.m_writer


class Event(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    day: date = Field(index=True)
    instant: datetime = Field(unique=True)


def test_temporal_export_preserves_existing_schema_and_requires_no_installation(store):
    db = Database(store, Registry(Event))
    source = db.m_routine(Event, name="EventAPI")
    assert 'cfg("tag","day")="D"' in source
    assert 'cfg("tag","instant")="T"' in source
    assert not db.inspect_schema(Event).installed


@pytest.fixture
def temporal_writer(store, m_writer):
    _, run, routine, _ = m_writer

    def bind(model=Event):
        db = Database(store, Registry(model))
        replace_source(routine, db.m_routine(model, name="YHMFTAPI"))
        return db, run

    return bind


@pytest.mark.native
@pytest.mark.parametrize(
    "day", ["0001-01-01", "1900-02-28", "2000-02-29", "2024-02-29", "9999-12-31"]
)
def test_temporal_nodes_indexes_and_unique_claims_match_python(store, temporal_writer, day):
    db, run = temporal_writer()
    instant = day + "T23:59:59.999999Z"
    assert run("insert", {"id": "A", "day": day, "instant": instant}) == "ok"
    expected = MemoryStore()
    Database(expected, Registry(Event)).insert(
        Event(id="A", day=date.fromisoformat(day), instant=datetime.fromisoformat(instant))
    )
    roots = (Event.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected, roots)
    before = dump(store, roots)
    assert run("insert", {"id": "B", "day": day, "instant": instant}) == "unique"
    assert dump(store, roots) == before
    assert db.get(Event, "A").instant == datetime.fromisoformat(instant)
    assert db.check_indexes(Event).issues == ()
    assert run("delete", {"id": "A"}) == "ok"
    assert db.check_indexes(Event).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "day",
    [
        "0000-01-01",
        "1900-02-29",
        "2023-02-29",
        "2024-04-31",
        "2024-00-01",
        "2024-13-01",
        "2024-01-00",
        "2024-1-01",
        "2024-01-1",
        "20240101",
        "2024-01-01Z",
    ],
)
def test_invalid_calendar_input_leaves_no_metadata_or_data(store, temporal_writer, day):
    db, run = temporal_writer()
    assert (
        run("insert", {"id": "A", "day": day, "instant": "2024-01-01T00:00:00.000000Z"})
        == "invalid"
    )
    assert not db.inspect_schema(Event).installed
    assert dump(store, (Event.__global__, "^YHMFIndex", "^YHMFUnique")) == {}


@pytest.mark.native
@pytest.mark.parametrize(
    "instant",
    [
        "2023-02-29T00:00:00.000000Z",
        "2024-01-01T24:00:00.000000Z",
        "2024-01-01T00:60:00.000000Z",
        "2024-01-01T00:00:60.000000Z",
        "2024-01-01T00:00:00Z",
        "2024-01-01T00:00:00.000000+00:00",
        "2024-01-01T00:00:00.000000",
        "2024-01-01 00:00:00.000000Z",
        "2024-01-01T00:00:00.00000Z",
    ],
)
def test_noncanonical_utc_wire_inputs_leave_no_state(store, temporal_writer, instant):
    db, run = temporal_writer()
    assert run("insert", {"id": "A", "day": "2024-01-01", "instant": instant}) == "invalid"
    assert not db.inspect_schema(Event).installed


@pytest.mark.native
def test_temporal_bounds_use_calendar_order_and_failed_replace_rolls_back(store, temporal_writer):
    class Bounded(Event):
        day: date = Field(ge=date(2000, 1, 1), lt=date(2100, 1, 1), index=True)
        instant: datetime = Field(
            gt=datetime(2000, 1, 1, tzinfo=UTC), le=datetime(2100, 1, 1, tzinfo=UTC), unique=True
        )

    db, run = temporal_writer(Bounded)
    values = {"id": "A", "day": "2000-01-01", "instant": "2000-01-01T00:00:00.000001Z"}
    assert run("insert", values) == "ok"
    roots = (Event.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    before = dump(store, roots)
    for field, value in (
        ("day", "1999-12-31"),
        ("day", "2100-01-01"),
        ("instant", "2000-01-01T00:00:00.000000Z"),
        ("instant", "2100-01-01T00:00:00.000001Z"),
    ):
        with pytest.raises(ValidationError):
            Bounded.model_validate(values | {field: value})
        assert run("replace", values | {field: value}) == "invalid"
        assert dump(store, roots) == before
    assert run("replace", values | {"instant": "2100-01-01T00:00:00.000000Z"}) == "ok"
    assert db.check_indexes(Bounded).issues == ()


@pytest.mark.native
def test_existing_python_offset_datetime_is_read_as_canonical_utc(temporal_writer):
    db, run = temporal_writer()
    value = datetime(2024, 1, 1, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    db.insert(Event(id="A", day=date(2024, 1, 1), instant=value))
    assert (
        run("replace", {"id": "A", "day": "2024-02-29", "instant": "2023-12-31T23:30:00.000000Z"})
        == "ok"
    )
    assert db.get(Event, "A").instant == value
    assert db.check_indexes(Event).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "field,raw",
    [
        ("day", b"D:1900-02-29"),
        ("instant", b"T:2024-01-01T00:00:60.000000Z"),
        ("instant", b"T:2024-01-01T00:00:00.000000"),
    ],
)
def test_invalid_stored_temporal_values_are_corrupt_and_unchanged(
    store, temporal_writer, field, raw
):
    db, run = temporal_writer()
    db.insert(Event(id="A", day=date(2024, 1, 1), instant=datetime(2024, 1, 1, tzinfo=UTC)))
    store.set(db.registry.spec(Event).path("A").child(field.encode()), raw)
    roots = (Event.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    before = dump(store, roots)
    assert run("delete", {"id": "A"}) == "corrupt"
    assert dump(store, roots) == before


@pytest.mark.native
@settings(
    max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(day=st.dates(), instant=st.datetimes(timezones=st.just(UTC)))
def test_generated_calendar_and_utc_instants_match_python_codec(temporal_writer, day, instant):
    from mccann_hub.yhmf.codecs import encode_scalar

    _, run = temporal_writer()
    assert (
        run("temporal", {"day": day.isoformat(), "instant": encode_scalar(instant)[2:].decode()})
        == "1|1"
    )


@pytest.mark.native
def test_owned_temporal_patch_uses_the_same_codecs_and_index_rules(store, m_writer):
    from tests.test_m_choice_writers import ChoiceBin, ChoiceLocation, ChoiceRoot

    class OwnedEvent(Event):
        __global__ = None

    class Bin(ChoiceBin):
        balances: Children[OwnedEvent] = ChildField(key="id")

    class Location(ChoiceLocation):
        bins: Children[Bin] = ChildField(key="id")

    class Root(ChoiceRoot):
        locations: Children[Location] = ChildField(key="id")

    db = Database(store, Registry(Root))
    db.insert(Root(id="MAIN", locations={"DOCK": Location(id="DOCK", bins={"B1": Bin(id="B1")})}))
    _, run, routine, _ = m_writer
    replace_source(routine, db.m_routine(Root, name="YHMFTAPI"))
    assert (
        run(
            "ownedinsert",
            {"id": "LOT-001", "day": "2000-02-29", "instant": "2000-02-29T00:00:00.000000Z"},
        )
        == "ok"
    )
    assert run("ownedpatch", {"day": "2024-02-29"}) == "ok"
    assert db.get(
        Root, "MAIN", at=("locations", "DOCK", "bins", "B1", "balances", "LOT-001")
    ).day == date(2024, 2, 29)
    assert db.check_indexes(Root).issues == ()
    assert run("owneddelete", {}) == "ok"
    assert db.check_indexes(Root).issues == ()
