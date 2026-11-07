# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path as FilePath

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, NativeStore, Registry
from mccann_hub.yhmf.codecs import decode_scalar, encode_scalar
from mccann_hub.yhmf.errors import MigrationError, SchemaError, StorageError, UniqueViolation
from mccann_hub.yhmf.schema_diff import compare_schemas
from mccann_hub.yhmf.schemas import SchemaSnapshot


class Event(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    day: date = Field(ge=date(2000, 1, 1), index=True)
    instant: datetime | None = Field(default=None, unique=True)


def test_temporal_golden_encodings_are_fixed_and_self_describing():
    cases = json.loads((FilePath(__file__).with_name("fixtures") / "temporal-v1.json").read_text())
    for case in cases:
        kind = date if case["kind"] == "date" else datetime
        value = kind.fromisoformat(case["input"])
        encoded = case["encoded"].encode("ascii")
        assert encode_scalar(value) == encoded
        result = decode_scalar(encoded)
        assert type(result) is kind and result == value
        assert encode_scalar(result) == encoded


@given(st.dates())
def test_calendar_dates_roundtrip(value):
    assert decode_scalar(encode_scalar(value)) == value


@given(st.datetimes(timezones=st.just(UTC)))
def test_utc_instants_roundtrip(value):
    assert decode_scalar(encode_scalar(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        b"D:2024-2-29",
        b"D:2023-02-29",
        b"D:20240229",
        b"D:0000-01-01",
        b"T:2026-11-06T12:00:00Z",
        b"T:2026-11-06T12:00:00.000000+00:00",
        b"T:2026-11-06T12:00:00.000000",
        b"T:2026-11-06T12:00:60.000000Z",
    ],
)
def test_noncanonical_temporal_storage_is_rejected(value):
    with pytest.raises(StorageError):
        decode_scalar(value)


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, tzinfo=timezone(timedelta(hours=-1))),
    ],
)
def test_naive_and_out_of_calendar_instants_are_rejected(value):
    with pytest.raises(StorageError):
        encode_scalar(value)


def test_iso_payloads_sort_by_calendar_and_utc_instant():
    values = [
        datetime(2026, 1, 2, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=2))),
    ]
    assert sorted(values, key=encode_scalar) == sorted(values)


def test_temporal_fields_patch_query_and_claim_unique_instants_atomically(store):
    db = Database(store, Registry(Event))
    instant = datetime(2026, 11, 6, 12, tzinfo=timezone(timedelta(hours=2)))
    db.insert(Event(id="A", day=date(2026, 11, 6), instant=instant))
    stored = db.get(Event, "A")
    assert stored.instant == instant and stored.instant.tzinfo is UTC
    assert [row.id for row in db.query(Event).where(instant=instant.astimezone(UTC)).all()] == ["A"]
    with pytest.raises(UniqueViolation):
        db.insert(Event(id="B", day=date(2026, 11, 6), instant=instant.astimezone(UTC)))
    with pytest.raises(ValidationError):
        db.patch(Event, "A", changes={"day": date(1999, 12, 31)})
    with pytest.raises(StorageError):
        db.patch(Event, "A", changes={"instant": datetime(2026, 1, 1)})
    db.patch(Event, "A", changes={"day": date(2026, 11, 7), "instant": None})
    assert db.query(Event).where(day=date(2026, 11, 6)).all() == []
    assert [row.id for row in db.query(Event).where(day=date(2026, 11, 7)).all()] == ["A"]
    assert db.check_indexes(Event).issues == ()


def test_temporal_schema_uses_owned_codec_and_bound_metadata():
    snapshot = Registry(Event).spec(Event).snapshot
    assert snapshot == (
        FilePath(__file__).with_name("fixtures") / "temporal-schema-v1.json"
    ).read_bytes().rstrip(b"\n")
    document = json.loads(snapshot)
    day = document["declaration"]["properties"]["day"]
    assert day["codec"] == "date-v1" and day["temporal_minimum"] == "2000-01-01"
    assert document["codec_version"] == 1 and document["layout"] == 1


def test_unknown_temporal_codec_is_not_treated_as_compatible():
    from mccann_hub.yhmf.codecs import canonical_json

    snapshot = SchemaSnapshot(Registry(Event).spec(Event).snapshot)
    changed = snapshot.to_dict()
    changed["declaration"]["properties"]["day"]["codec"] = "date-v999"
    difference = compare_schemas(snapshot, SchemaSnapshot(canonical_json(changed)))
    assert any(change.kind == "unsupported_schema" for change in difference)


def test_temporal_literal_additions_and_constraint_validation_use_existing_migrations(store):
    class Before(Model):
        __global__ = Event.__global__
        id: str = Field(primary_key=True)

    class After(Before):
        __schema_version__ = 2
        day: date = date(2026, 1, 1)
        instant: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    Database(store, Registry(Before)).insert(Before(id="A"))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete"
    assert db.get(After, "A").instant == datetime(2026, 1, 1, tzinfo=UTC)
    assert db.get(After, "A").day == date(2026, 1, 1)

    class Invalid(After):
        day: date = Field(ge=date(2027, 1, 1))

    next_db = Database(store, Registry(Invalid))
    status = next_db.begin_migration(After, Invalid, plan=next_db.plan_migration(Invalid))
    with pytest.raises(MigrationError):
        next_db.advance_migration(status.migration_id, After, Invalid)
    assert next_db.migration_status(status.migration_id).phase == "aborted"
    assert db.get(After, "A").day == date(2026, 1, 1)


def test_nested_temporal_values_remain_directly_addressable(store):
    class Child(Model):
        id: int = Field(primary_key=True)
        at: datetime

    class Parent(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Parent))
    db.insert(Parent(id=1, children={1: Child(id=1, at=datetime(2026, 1, 1, tzinfo=UTC))}))
    db.patch(Parent, 1, at=("children", 1), changes={"at": datetime(2027, 1, 1, tzinfo=UTC)})
    assert db.get(Parent, 1, at=("children", 1)).at.year == 2027


def test_naive_schema_defaults_are_rejected_before_metadata_installation():
    class Invalid(Event):
        instant: datetime = datetime(2026, 1, 1)

    with pytest.raises(SchemaError, match="aware"):
        Registry(Invalid)


def test_unindexed_naive_insertion_cannot_leave_schema_metadata_installed(store):
    from mccann_hub.yhmf.schemas import schema_path

    class Plain(Model):
        __global__ = Event.__global__
        id: str = Field(primary_key=True)
        instant: datetime

    db = Database(store, Registry(Plain))
    with pytest.raises(StorageError, match="timezone"):
        db.insert(Plain(id="A", instant=datetime(2026, 1, 1)))
    assert store.get(schema_path(Plain.__global__)) is None


@pytest.mark.native
def test_plain_m_reads_and_compares_temporal_encodings(store, tmp_path):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires a real M runtime")
    db = Database(store, Registry(Event))
    db.insert(Event(id="A", day=date(2026, 1, 1), instant=datetime(2026, 1, 1, tzinfo=UTC)))
    (tmp_path / "YHMFTEMP.m").write_text("""YHMFTEMP
 new d,t
 set d=^YHMFTestProduct("s:A","day"),t=^YHMFTestProduct("s:A","instant")
 write d,!,t,!
 write (d]]"D:2025-12-31"),!
 quit
""")
    environment = dict(os.environ)
    environment["ydb_routines"] = str(tmp_path) + " " + environment["ydb_routines"]
    result = subprocess.run(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "YHMFTEMP"],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert result.stdout.splitlines() == ["D:2026-01-01", "T:2026-01-01T00:00:00.000000Z", "1"]
