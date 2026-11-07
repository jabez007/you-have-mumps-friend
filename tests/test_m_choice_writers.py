# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite M inputs preserve the compiled Python choice domain and storage bytes."""

from enum import IntEnum, StrEnum
from typing import Annotated, Literal

import pytest
from pydantic import AfterValidator, create_model

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry
from mccann_hub.yhmf.errors import SchemaError
from mccann_hub.yhmf.schemas import schema_path
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_writers.m_writer


class State(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Rank(IntEnum):
    LOW = -1
    HIGH = 2


class ChoiceRow(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    kind: Literal["receive", "move", "adjust"] = Field(unique=True)
    state: State = Field(index=True)
    rank: Rank = Field(index=True)


def test_export_accepts_finite_string_integer_domains_without_installing_schema(store):
    db = Database(store, Registry(ChoiceRow))
    source = db.m_routine(ChoiceRow, name="ChoiceAPI")
    assert 'cfg("choicecount","kind")="3"' in source
    assert source == db.m_routine(ChoiceRow, name="ChoiceAPI")
    assert max(map(len, source.splitlines())) < 1000
    assert not db.inspect_schema(ChoiceRow).installed


@pytest.mark.parametrize(
    "annotation",
    [Literal[10**18], Literal[True], Literal["x" * 32765]],
)
def test_unqualified_choice_domains_fail_at_export(store, annotation):
    model = create_model("Unsupported", __base__=ChoiceRow, kind=(annotation, Field()))
    db = Database(store, Registry(model))
    with pytest.raises(SchemaError, match="M choice"):
        db.m_routine(model, name="ChoiceAPI")
    assert store.get(schema_path(ChoiceRow.__global__)) is None


def test_choice_custom_validation_is_never_bypassed(store):
    annotation = Annotated[Literal["open", "closed"], AfterValidator(lambda value: value)]
    model = create_model("Customized", __base__=ChoiceRow, kind=(annotation, Field()))
    with pytest.raises(SchemaError, match="custom validation"):
        Database(store, Registry(model)).m_routine(model, name="ChoiceAPI")


def test_choice_export_has_explicit_count_and_byte_bounds(store):
    for values in (tuple(str(i) for i in range(257)), ("x" * 20000, "y" * 20000)):
        annotation = Literal[values]
        model = create_model("Large", __base__=ChoiceRow, kind=(annotation, Field()))
        with pytest.raises(SchemaError, match="M choice"):
            Database(store, Registry(model)).m_routine(model, name="ChoiceAPI")


@pytest.mark.native
def test_choice_writes_match_python_indexes_and_reject_invalid_inputs(store, m_writer):
    _, run, routine, _ = m_writer
    db = Database(store, Registry(ChoiceRow))
    replace_source(routine, db.m_routine(ChoiceRow, name="YHMFTAPI"))
    values = {"id": "A", "kind": "receive", "state": "open", "rank": "-1"}
    assert run("insert", values) == "ok"
    expected = MemoryStore()
    Database(expected, Registry(ChoiceRow)).insert(
        ChoiceRow(id="A", kind="receive", state=State.OPEN, rank=Rank.LOW)
    )
    roots = (ChoiceRow.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected, roots)
    before = dump(store, roots)
    for field, invalid in (("kind", "unknown"), ("state", "OPEN"), ("rank", "1")):
        assert run("replace", values | {field: invalid}) == "invalid"
        assert dump(store, roots) == before
    assert run("insert", values | {"id": "B"}) == "unique"
    assert dump(store, roots) == before
    assert run("replace", values | {"kind": "move", "state": "closed", "rank": "2"}) == "ok"
    assert db.get(ChoiceRow, "A").rank is Rank.HIGH
    assert db.query(ChoiceRow).where(state=State.CLOSED).all()[0].id == "A"
    assert db.check_indexes(ChoiceRow).issues == ()
    store.set(db.registry.spec(ChoiceRow).path("A").child(b"kind"), b"s:unknown")
    before = dump(store, roots)
    assert run("delete", {"id": "A"}) == "corrupt"
    assert dump(store, roots) == before


@pytest.mark.native
@pytest.mark.parametrize("text", ["", 'quote"\\', "\x00\t\n\x7f", "x" * 2000])
def test_choice_literals_escape_controls_and_chunk_generated_constants(store, m_writer, text):
    model = create_model(
        "Escaped", __base__=Model, id=(str, Field(primary_key=True)), value=(Literal[text], Field())
    )
    model.__global__ = ChoiceRow.__global__
    db = Database(store, Registry(model))
    _, run, routine, _ = m_writer
    source = db.m_routine(model, name="YHMFTAPI")
    assert max(map(len, source.splitlines())) < 1000
    replace_source(routine, source)
    assert run("insert", {"id": "A", "value": text}) == "ok"
    assert db.get(model, "A").value == text
    assert run("replace", {"id": "A", "value": text + "!"}) == "invalid"


class ChoiceChild(ChoiceRow):
    __global__ = None


class ChoiceBin(Model):
    id: str = Field(primary_key=True)
    balances: Children[ChoiceChild] = ChildField(key="id")


class ChoiceLocation(Model):
    id: str = Field(primary_key=True)
    bins: Children[ChoiceBin] = ChildField(key="id")


class ChoiceRoot(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    locations: Children[ChoiceLocation] = ChildField(key="id")


def test_owned_choice_compilation_reuses_qualified_scalar_rules(store):
    source = Database(store, Registry(ChoiceRoot)).m_routine(ChoiceRoot, name="ChoiceAPI")
    assert 'cfg("nodes","locations.bins.balances","choicecount","kind")="3"' in source


@pytest.mark.native
def test_owned_choice_patch_preserves_siblings_and_indexes(store, m_writer):
    db = Database(store, Registry(ChoiceRoot))
    db.insert(
        ChoiceRoot(
            id="MAIN",
            locations={"DOCK": ChoiceLocation(id="DOCK", bins={"B1": ChoiceBin(id="B1")})},
        )
    )
    _, run, routine, _ = m_writer
    replace_source(routine, db.m_routine(ChoiceRoot, name="YHMFTAPI"))
    assert (
        run("ownedinsert", {"id": "LOT-001", "kind": "receive", "state": "open", "rank": "-1"})
        == "ok"
    )
    assert run("ownedpatch", {"state": "closed", "rank": "2"}) == "ok"
    at = ("locations", "DOCK", "bins", "B1", "balances", "LOT-001")
    value = db.get(ChoiceRoot, "MAIN", at=at)
    assert value.state is State.CLOSED and value.rank is Rank.HIGH and value.kind == "receive"
    before = dump(store, (ChoiceRoot.__global__, "^YHMFIndex", "^YHMFUnique"))
    assert run("ownedpatch", {"kind": "unknown"}) == "invalid"
    assert dump(store, (ChoiceRoot.__global__, "^YHMFIndex", "^YHMFUnique")) == before
    assert db.check_indexes(ChoiceRoot).issues == ()
    assert run("owneddelete", {}) == "ok"
    assert db.count(ChoiceRoot, "MAIN", collection=at[:-1]) == 0
    assert db.check_indexes(ChoiceRoot).issues == ()


def test_enum_core_schema_validation_hook_is_rejected(store):
    from pydantic_core import core_schema

    class Customized(StrEnum):
        OPEN = "open"

        @classmethod
        def __get_pydantic_core_schema__(cls, source, handler):
            return core_schema.no_info_after_validator_function(
                lambda value: value, handler(source)
            )

    model = create_model("Hooked", __base__=ChoiceRow, state=(Customized, Field()))
    with pytest.raises(SchemaError, match="custom validation"):
        Database(store, Registry(model)).m_routine(model, name="ChoiceAPI")
