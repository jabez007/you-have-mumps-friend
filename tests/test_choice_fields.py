# SPDX-License-Identifier: GPL-3.0-or-later
import json
from enum import Enum, Flag, IntEnum, StrEnum
from pathlib import Path as FilePath
from typing import Literal

import pytest
from pydantic import ConfigDict, ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Registry, Transform
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import SchemaError, StorageError, UniqueViolation


class State(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    ALIAS = "open"


class Priority(IntEnum):
    LOW = -1
    HIGH = 2


class Switch(Enum):
    ON = True
    OFF = False


class Ticket(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    state: State | None = Field(default=State.OPEN, unique=True)
    priority: Priority = Field(default=Priority.LOW, index=True)
    enabled: Switch = Switch.ON
    kind: Literal["receipt", "issue"] = Field(default="receipt", index=True)
    rank: Literal[-1, 2] = -1
    flag: Literal[True] = True


def test_choice_schema_and_storage_golden(store):
    db = Database(store, Registry(Ticket))
    db.insert(Ticket(id="A"))
    record = db.get(Ticket, "A")
    assert record.state is State.OPEN and record.priority is Priority.LOW
    assert record.enabled is Switch.ON and record.kind == "receipt"
    fixture = json.loads((FilePath(__file__).with_name("fixtures") / "choices-v1.json").read_text())
    spec = db.registry.spec(Ticket)
    assert spec.snapshot == canonical_json(fixture["schema"])
    for name, raw in fixture["values"].items():
        assert store.get(spec.path("A").child(name.encode())) == raw.encode()


def test_enum_names_aliases_and_order_do_not_change_persistence_identity():
    class Other(Enum):
        FINISHED = "closed"
        STARTED = "open"

    class Equivalent(Ticket):
        state: Other | None = Field(default=Other.STARTED, unique=True)

    assert Registry(Equivalent).spec(Equivalent).snapshot == Registry(Ticket).spec(Ticket).snapshot


def test_choice_patch_save_query_and_rollback(store):
    db = Database(store, Registry(Ticket))
    db.insert(Ticket(id="A"))
    db.insert(Ticket(id="B", state=State.CLOSED))
    with pytest.raises(UniqueViolation):
        db.patch(Ticket, "A", changes={"state": State.CLOSED, "kind": "issue"})
    assert db.get(Ticket, "A").kind == "receipt"
    db.patch(Ticket, "B", changes={"state": None})
    db.patch(Ticket, "A", changes={"state": State.CLOSED, "kind": "issue"})
    value = db.get(Ticket, "A")
    db.save(value)
    assert db.query(Ticket).where(state=State.CLOSED).all()[0].id == "A"
    assert db.query(Ticket).where(kind="issue").all()[0].id == "A"
    assert db.query(Ticket).where(priority=Priority.LOW).all()[0].priority is Priority.LOW
    with pytest.raises(ValidationError):
        db.query(Ticket).where(state="closed").all()
    with pytest.raises(ValidationError):
        db.patch(Ticket, "A", changes={"kind": "unknown"})
    assert db.check_indexes(Ticket).issues == ()


@pytest.mark.parametrize(
    "field,raw", [("state", b"s:unknown"), ("priority", b"b:1"), ("kind", b"i:1")]
)
def test_unknown_or_wrongly_typed_stored_choices_are_rejected(store, field, raw):
    db = Database(store, Registry(Ticket))
    db.insert(Ticket(id="A"))
    store.set(db.registry.spec(Ticket).path("A").child(field.encode()), raw)
    with pytest.raises(StorageError, match="choice"):
        db.get(Ticket, "A")


def test_enum_missing_hook_is_never_a_storage_decoder(store):
    class Lenient(Enum):
        KNOWN = "known"

        @classmethod
        def _missing_(cls, value):
            raise AssertionError("Unknown stored data must not call application fallback code")

    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        value: Lenient

    db = Database(store, Registry(Owner))
    db.insert(Owner(id="A", value=Lenient.KNOWN))
    store.set(db.registry.spec(Owner).path("A").child(b"value"), b"s:unknown")
    with pytest.raises(StorageError, match="choice"):
        db.get(Owner, "A")


@pytest.mark.parametrize(
    "annotation",
    [
        Enum("Mixed", {"A": 1, "B": "x"}),
        Enum("Empty", {}),
        Flag("Bits", {"A": 1}),
        Literal[1, "x"],
        Literal[1.5],
        Enum("Numbers", {"A": 1.5}),
    ],
)
def test_unsupported_choice_declarations_are_rejected(annotation):
    from pydantic import create_model

    owner = create_model(
        "Owner", __base__=Model, id=(str, Field(primary_key=True)), value=(annotation, ...)
    )
    owner.__global__ = "^YHMFTestProduct"
    with pytest.raises(SchemaError, match="choice|Enum|Literal"):
        Registry(owner)


def test_use_enum_values_requires_an_explicit_revalidation_design():
    class Invalid(Ticket):
        model_config = ConfigDict(use_enum_values=True)

    with pytest.raises(SchemaError, match="use_enum_values"):
        Registry(Invalid)


def test_mutated_enum_members_cannot_change_the_compiled_storage_contract(store):
    class Mutable(Enum):
        A = "a"

    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        value: Mutable = Field(index=True)

    db = Database(store, Registry(Owner))
    db.insert(Owner(id="A", value=Mutable.A))
    Mutable.A._value_ = "changed"
    with pytest.raises(StorageError, match="choice"):
        db.get(Owner, "A")
    with pytest.raises(StorageError, match="choice"):
        db.query(Owner).where(value=Mutable.A).all()


def test_literal_order_is_irrelevant_but_constraints_are_not():
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        value: Literal["b", "a"]

    class Equivalent(Before):
        value: Literal["a", "b"]

    class Changed(Before):
        value: Literal["a"]

    assert (
        Registry(Before).spec(Before).fingerprint
        == Registry(Equivalent).spec(Equivalent).fingerprint
    )
    assert Registry(Before).spec(Before).fingerprint != Registry(Changed).spec(Changed).fingerprint


def test_owned_enum_and_literal_fields_use_existing_partial_validation(store):
    class Item(Model):
        id: str = Field(primary_key=True)
        state: State = Field(index=True)
        kind: Literal["a", "b"] = "a"

    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        state: State = Field(index=True)
        items: Children[Item] = ChildField(key="id")

    db = Database(store, Registry(Owner))
    db.insert(Owner(id="A", state=State.OPEN, items={"B": Item(id="B", state=State.OPEN)}))
    db.patch(Owner, "A", changes={"state": State.CLOSED})
    db.patch(Owner, "A", at=("items", "B"), changes={"state": State.CLOSED, "kind": "b"})
    assert db.get(Owner, "A").items["B"].state is State.CLOSED
    assert len(db.query(Owner).where(**{"items.state": State.CLOSED}).all()) == 1
    assert db.check_indexes(Owner).issues == ()


def test_enum_literal_defaults_migrate_without_record_transforms(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class After(Before):
        state: State = Field(default=State.OPEN, index=True)
        kind: Literal["a", "b"] = "a"

    Database(store, Registry(Before)).insert(Before(id="A"))
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.execution_supported
    status = db.begin_migration(Before, After, plan=plan)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete" and db.get(After, "A").state is State.OPEN


def test_changing_enum_domain_requires_an_explicit_transform(store):
    class OldState(Enum):
        OLD = "old"

    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        state: OldState

    class After(Before):
        state: State

    Database(store, Registry(Before)).insert(Before(id="A", state=OldState.OLD))
    db = Database(store, Registry(After))
    assert not db.plan_migration(After).execution_supported
    transform = Transform("ticket.state.v2", lambda old: After(id=old.id, state=State.OPEN))
    plan = db.plan_migration(After, transform=transform)
    status = db.begin_migration(Before, After, plan=plan, transform=transform)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After, transform=transform)
    assert status.phase == "complete" and db.get(After, "A").state is State.OPEN
