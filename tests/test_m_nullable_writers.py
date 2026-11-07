# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed M null inputs keep absence, null, and literal tag-like strings distinct."""

import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Literal

import pytest

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    Path,
    Registry,
)
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_writers.m_writer


class NullableRow(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    value: str | None = Field(default=None, unique=True)
    count: int | None = Field(default=None, ge=0, index=True)
    amount: Decimal | None = Field(default=None, ge=0, index=True)
    day: date | None = Field(default=None, index=True)
    instant: datetime | None = Field(default=None, unique=True)
    kind: Literal["open", "closed"] | None = Field(default=None, index=True)


def test_nullable_export_is_explicit_and_does_not_change_schema(store):
    db = Database(store, Registry(NullableRow))
    source = db.m_routine(NullableRow, name="NullableAPI")
    assert 'cfg("nullable","value")="1"' in source
    assert not db.inspect_schema(NullableRow).installed


@pytest.fixture
def nullable_writer(store, m_writer):
    _, _, routine, environment = m_writer
    db = Database(store, Registry(NullableRow))
    replace_source(routine, db.m_routine(NullableRow, name="YHMFTAPI"))
    root = Path("^YHMFTestStorage", (b"writerInput",))

    def run(operation, data):
        store.delete_tree(root)
        for field, value in data.items():
            path = root.child(field.encode())
            if value is None:
                store.set(path.child(b"%null"), b"1")
            elif isinstance(value, dict):
                for key, raw in value.items():
                    store.set(path.child(key.encode()), raw.encode())
            else:
                store.set(path, value.encode())
        result = subprocess.run(
            [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", operation + "^YHMFWTEST"],
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        assert not result.stderr, result.stderr
        return result.stdout.strip()

    return db, run


def nulls(key="A"):
    return {
        "id": key,
        "value": None,
        "count": None,
        "amount": None,
        "day": None,
        "instant": None,
        "kind": None,
    }


@pytest.mark.native
def test_null_nodes_indexes_and_multiple_null_unique_fields_match_python(store, nullable_writer):
    db, run = nullable_writer
    assert run("insert", nulls()) == run("insert", nulls("B")) == "ok"
    expected = MemoryStore()
    other = Database(expected, Registry(NullableRow))
    other.insert(NullableRow(id="A"))
    other.insert(NullableRow(id="B"))
    roots = (NullableRow.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected, roots)
    assert db.query(NullableRow).where(value=None).all()[0].value is None
    values = {
        "id": "A",
        "value": "n:",
        "count": "0",
        "amount": "1.50",
        "day": "2000-02-29",
        "instant": "2024-01-01T00:00:00.000000Z",
        "kind": "open",
    }
    assert run("replace", values) == "ok"
    other.save(
        NullableRow(
            id="A",
            value="n:",
            count=0,
            amount=Decimal("1.50"),
            day=date(2000, 2, 29),
            instant=datetime(2024, 1, 1, tzinfo=UTC),
            kind="open",
        )
    )
    assert dump(store, roots) == dump(expected, roots)
    before = dump(store, roots)
    assert run("replace", values | {"id": "B"}) == "unique"
    assert dump(store, roots) == before
    assert run("replace", nulls()) == "ok"
    other.save(NullableRow(id="A"))
    assert dump(store, roots) == dump(expected, roots)
    assert db.check_indexes(NullableRow).issues == ()
    assert run("delete", {"id": "A"}) == run("delete", {"id": "B"}) == "ok"
    assert db.check_indexes(NullableRow).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("text", ["", "n:", "v:", "%null", "n:value"])
def test_nullable_strings_never_use_a_text_sentinel(nullable_writer, text):
    db, run = nullable_writer
    assert run("insert", nulls() | {"value": text}) == "ok"
    assert db.get(NullableRow, "A").value == text


@pytest.mark.native
@pytest.mark.parametrize(
    "value", [{"%null": "0"}, {"%null": "01"}, {"%null": "1", "extra": "x"}, {"wrong": "1"}]
)
def test_invalid_null_shapes_and_missing_nullable_fields_leave_no_state(nullable_writer, value):
    db, run = nullable_writer
    assert run("insert", nulls() | {"value": value}) == "invalid"
    data = nulls()
    del data["value"]
    assert run("insert", data) == "invalid"
    assert not db.inspect_schema(NullableRow).installed


@pytest.mark.native
def test_null_is_not_accepted_for_nonnullable_keys_or_field_constraints(nullable_writer):
    db, run = nullable_writer
    assert run("insert", nulls() | {"id": None}) == "invalid"
    assert run("insert", nulls() | {"count": "-1"}) == "invalid"
    assert run("insert", nulls() | {"kind": "unknown"}) == "invalid"
    assert not db.inspect_schema(NullableRow).installed


@pytest.mark.native
def test_owned_nullable_patches_match_python_and_survive_native_restarts(
    store, nullable_writer, m_writer
):
    from tests.test_m_choice_writers import ChoiceBin, ChoiceLocation, ChoiceRoot

    class Child(NullableRow):
        __global__ = None

    class Bin(ChoiceBin):
        balances: Children[Child] = ChildField(key="id")

    class Location(ChoiceLocation):
        bins: Children[Bin] = ChildField(key="id")

    class Root(ChoiceRoot):
        locations: Children[Location] = ChildField(key="id")

    db = Database(store, Registry(Root))
    expected = Database(MemoryStore(), Registry(Root))
    root = Root(id="MAIN", locations={"DOCK": Location(id="DOCK", bins={"B1": Bin(id="B1")})})
    db.insert(root)
    expected.insert(root)
    _, run = nullable_writer
    _, _, routine, _ = m_writer
    source = db.m_routine(Root, name="YHMFTAPI")
    marker = " ; YHMF owned mutation complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    collection = ("locations", "DOCK", "bins", "B1", "balances")
    assert run("ownedinsert", nulls("LOT-001")) == "ok"
    expected.insert_child(Root, "MAIN", collection=collection, record=Child(id="LOT-001"))
    for value in ("n:", None, ""):
        assert run("ownedpatch", {"value": value}) == "ok"
        expected.patch(Root, "MAIN", at=collection + ("LOT-001",), changes={"value": value})
        roots = (Root.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
        assert dump(store, roots) == dump(expected.store, roots)
        assert db.check_indexes(Root).issues == ()
    assert run("owneddelete", {}) == "ok"
    expected.delete_child(Root, "MAIN", at=collection + ("LOT-001",))
    assert dump(store, roots) == dump(expected.store, roots)
