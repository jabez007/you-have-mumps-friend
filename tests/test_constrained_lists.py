# SPDX-License-Identifier: GPL-3.0-or-later
import json
from datetime import date
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Annotated, Literal

import pytest
from pydantic import AfterValidator, ValidationError, create_model

from mccann_hub.yhmf import Database, Field, Model, Registry, Transform
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import MigrationError, SchemaError


class ConstrainedList(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    values: list[Annotated[int, Field(ge=0, le=100)] | None] = Field(index=True)
    labels: list[Annotated[str, Field(min_length=1, max_length=8)]]


def test_constrained_list_golden_and_validation(store):
    db = Database(store, Registry(ConstrainedList))
    record = ConstrainedList(id="A", values=[0, None, 100], labels=["雪", "x"])
    db.insert(record)
    fixture = json.loads(
        (FilePath(__file__).with_name("fixtures") / "constrained-list-v1.json").read_text()
    )
    spec = db.registry.spec(ConstrainedList)
    assert spec.snapshot == canonical_json(fixture["schema"])
    assert db.get(ConstrainedList, "A") == record
    for parts, value in fixture["nodes"]:
        assert store.get(spec.path("A").child(*(p.encode() for p in parts))) == value.encode()
    for values in ([-1], [101], [True]):
        with pytest.raises(ValidationError):
            db.patch(ConstrainedList, "A", changes={"values": values})
        with pytest.raises(ValidationError):
            db.query(ConstrainedList).where(values=values)
    with pytest.raises(ValidationError):
        db.patch(ConstrainedList, "A", changes={"labels": [""]})
    assert db.get(ConstrainedList, "A") == record
    assert db.check_indexes(ConstrainedList).issues == ()
    store.set(spec.path("A").child(b"values", b"1"), b"i:-1")
    with pytest.raises(ValidationError):
        db.get(ConstrainedList, "A")


@pytest.mark.parametrize(
    "item,valid,invalid",
    [
        (
            Annotated[Decimal, Field(max_digits=4, decimal_places=2)],
            Decimal("12.50"),
            Decimal("123"),
        ),
        (Annotated[date, Field(ge=date(2020, 1, 1))], date(2020, 1, 1), date(2019, 12, 31)),
        (Annotated[Literal["a", "b"], Field(min_length=1)], "a", "c"),
        (Annotated[str | None, Field(min_length=1)], None, ""),
    ],
)
def test_annotated_items_reuse_full_field_validation_and_codecs(store, item, valid, invalid):
    owner = create_model(
        "Owner", __base__=Model, id=(str, Field(primary_key=True)), values=(list[item], ...)
    )
    owner.__global__ = "^YHMFTestProduct"
    db = Database(store, Registry(owner))
    db.insert(owner(id="A", values=[valid]))
    assert db.get(owner, "A").values == [valid]
    with pytest.raises(ValidationError):
        db.patch(owner, "A", changes={"values": [invalid]})
    assert db.get(owner, "A").values == [valid]


def test_annotated_item_custom_validation_keeps_explicit_revision_policy(store):
    def even(value):
        if value % 2:
            raise ValueError("must be even")
        return value

    class Owner(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        values: list[Annotated[int, AfterValidator(even)]]

    registry = Registry(Owner)
    items = json.loads(registry.spec(Owner).snapshot)["declaration"]["properties"]["values"][
        "items"
    ]
    assert items["custom_validation"] is True
    db = Database(store, registry)
    db.insert(Owner(id="A", values=[2]))
    with pytest.raises(ValidationError):
        db.patch(Owner, "A", changes={"values": [3]})


@pytest.mark.parametrize("annotation", [list[list[str]], dict[str, int], int | str])
def test_unsupported_containers_name_the_owned_model_alternative(annotation):
    owner = create_model(
        "Owner", __base__=Model, id=(str, Field(primary_key=True)), value=(annotation, ...)
    )
    owner.__global__ = "^YHMFTestProduct"
    with pytest.raises(SchemaError, match=r"Children\[Model\]"):
        Registry(owner)


def test_constraint_placement_and_presentation_do_not_change_identity():
    class Outer(ConstrainedList):
        values: list[Annotated[int | None, Field(ge=0, le=100, description="display")]] = Field(
            index=True
        )

    assert (
        Registry(Outer).spec(Outer).snapshot
        == Registry(ConstrainedList).spec(ConstrainedList).snapshot
    )


def test_list_item_constraints_require_explicit_migration_and_validate_existing_data(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        values: list[int] = Field(index=True)

    class After(Before):
        values: list[Annotated[int, Field(ge=0)]] = Field(index=True)

    Database(store, Registry(Before)).insert(Before(id="A", values=[-1, 2]))
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.validation_only
    assert any(change.kind == "validation_changed" for change in plan.changes)
    status = db.begin_migration(Before, After, plan=plan)
    with pytest.raises(MigrationError):
        for _ in range(20):
            status = db.advance_migration(status.migration_id, Before, After)
    db.abort_migration(status.migration_id)
    assert Database(store, Registry(Before)).get(Before, "A").values == [-1, 2]
    transform = Transform("nonnegative-list.v1", lambda old: After(id=old.id, values=[0, 2]))
    plan = db.plan_migration(After, transform=transform)
    status = db.begin_migration(Before, After, plan=plan, transform=transform)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After, transform=transform)
    assert status.phase == "complete"
    assert db.get(After, "A").values == [0, 2]
    assert db.check_indexes(After).issues == ()
