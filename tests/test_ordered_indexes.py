# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from enum import IntEnum, StrEnum
from pathlib import Path as FilePath
from typing import Literal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import create_model

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    NativeStore,
    Path,
    Registry,
    Transform,
)
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import MigrationError, SchemaError, StorageError, UniqueViolation
from mccann_hub.yhmf.indexes import definitions, index_root
from mccann_hub.yhmf.ordered import value_key
from mccann_hub.yhmf.schema_diff import compare_schemas
from mccann_hub.yhmf.schemas import SchemaSnapshot
from tests.adversarial_support import dump


class Entry(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    amount: Decimal | None = Field(default=None, ordered=True, unique=True)
    text: str | None = Field(default=None, ordered=True)
    day: date = Field(default=date(2026, 11, 6), ordered=True)


def test_ordered_codec_and_schema_goldens():
    fixture = json.loads(
        (FilePath(__file__).with_name("fixtures") / "ordered-index-v1.json").read_text()
    )
    assert Registry(Entry).spec(Entry).snapshot == canonical_json(fixture["schema"])
    types = {
        "decimal": Decimal,
        "str": str,
        "date": date.fromisoformat,
        "datetime": datetime.fromisoformat,
        "int": int,
        "null": lambda value: None,
    }
    for case in fixture["values"]:
        assert value_key(types[case["kind"]](case["value"])) == case["encoded"].encode("utf-8")


@given(
    st.lists(
        st.decimals(
            min_value="-1000000",
            max_value="1000000",
            places=6,
            allow_nan=False,
            allow_infinity=False,
        ),
        min_size=1,
        max_size=20,
        unique=True,
    )
)
def test_ordered_numeric_subscripts_match_exact_decimal_order(values):
    store = MemoryStore()
    root = Path("^YHMFTestStorage")
    for value in values:
        store.set(root.child(value_key(value)), str(value).encode())
    keys = store.children(root, limit=100)
    assert [Decimal(store.get(root.child(key)).decode()) for key in keys] == sorted(values)


def test_numeric_index_encoding_does_not_depend_on_decimal_context():
    value = Decimal("999999999999.999999")
    with localcontext() as context:
        context.prec = 2
        assert value_key(value) == b"999999999999.999999"
        assert value_key(Decimal("-0.000000")) == b"0"
        assert value_key(Decimal("1E-18")) == b".000000000000000001"


@pytest.mark.parametrize(
    "value",
    [
        Decimal("1E18"),
        Decimal("-1E18"),
        Decimal("1E-19"),
        Decimal(".1234567890123456789"),
        Decimal("NaN"),
        Decimal("Infinity"),
        10**100,
    ],
)
def test_unrepresentable_ordered_numeric_values_fail_without_rounding(value):
    with pytest.raises(StorageError, match="ordered|Ordered"):
        value_key(value)


def test_native_numeric_value_order_equality_claims_and_nulls(store):
    db = Database(store, Registry(Entry))
    values = [Decimal("10"), Decimal("-1"), Decimal(".1"), Decimal("0"), None]
    for i, value in enumerate(values):
        db.insert(Entry(id=str(i), amount=value))
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"amount"))
    assert store.children(root, limit=100) == (b"-1", b"0", b".1", b"10", b"z:null")
    assert db.query(Entry).where(amount=Decimal(".10")).all()[0].id == "2"
    assert db.query(Entry).where(amount=None).all()[0].id == "4"
    assert db.query(Entry).where(amount=Decimal(".10")).occurrences()[0].record.amount == Decimal(
        ".1"
    )
    with pytest.raises(UniqueViolation):
        db.insert(Entry(id="duplicate", amount=Decimal("0.10")))
    db.patch(Entry, "2", changes={"amount": Decimal("12.500")})
    assert db.query(Entry).where(amount=Decimal(".1")).all() == []
    assert db.query(Entry).where(amount=Decimal("12.5")).all()[0].id == "2"
    assert db.check_indexes(Entry).issues == ()


def test_native_string_order_handles_empty_unicode_nul_and_control_values(store):
    db = Database(store, Registry(Entry))
    values = [None, "雪", "a\x01", "", "a\x00", "a\x02", "a", "aa", "😀"]
    for i, value in enumerate(values):
        db.insert(Entry(id=str(i), text=value))
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"text"))
    assert store.children(root, limit=100) == tuple(
        value_key(value) for value in sorted(value for value in values if value is not None)
    ) + (b"z:null",)
    assert db.query(Entry).where(text="a\x00").all()[0].text == "a\x00"
    assert db.check_indexes(Entry).issues == ()


@pytest.mark.parametrize(
    "annotation,values",
    [
        (date, [date.min, date(2026, 11, 6), date.max]),
        (datetime, [datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 1, tzinfo=UTC)]),
    ],
)
def test_temporal_ordered_indexes_use_calendar_and_utc_order(store, annotation, values):
    model = create_model(
        "Temporal",
        __base__=Model,
        id=(str, Field(primary_key=True)),
        at=(annotation, Field(ordered=True, unique=True)),
    )
    model.__global__ = "^YHMFTestProduct"
    db = Database(store, Registry(model))
    for i, value in enumerate(reversed(values)):
        db.insert(model(id=str(i), at=value))
    root = Path("^YHMFIndex", (b"YHMFTestProduct", b"at"))
    assert store.children(root, limit=100) == tuple(value_key(value) for value in values)
    if annotation is datetime:
        with pytest.raises(UniqueViolation):
            db.insert(model(id="duplicate", at=values[0].astimezone(timezone(timedelta(hours=2)))))


def test_failed_ordered_domain_patch_preserves_records_claims_and_metadata(store):
    db = Database(store, Registry(Entry))
    db.insert(Entry(id="A", amount=Decimal("1")))
    before = dump(store, (Entry.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique"))
    with pytest.raises(StorageError, match="ordered-v1"):
        db.patch(Entry, "A", changes={"amount": Decimal("1E18"), "text": "rejected"})
    assert dump(store, (Entry.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")) == before


@pytest.mark.parametrize("annotation", [bool, float, UUID, bytes, list[str]])
def test_unsupported_ordered_field_types_fail_registration(annotation):
    owner = create_model(
        "Owner",
        __base__=Model,
        id=(str, Field(primary_key=True)),
        value=(annotation, Field(ordered=True)),
    )
    owner.__global__ = "^YHMFTestProduct"
    with pytest.raises(SchemaError, match="Ordered"):
        Registry(owner)


@pytest.mark.parametrize("function", [Field, ChildField])
@pytest.mark.parametrize("value", [None, "ascending", 1])
def test_ordered_declaration_flags_are_explicit_booleans(function, value):
    with pytest.raises(SchemaError, match="boolean"):
        function(ordered=value, **({"key": "id"} if function is ChildField else {}))


def test_unknown_index_codec_versions_require_review():
    before = SchemaSnapshot(Registry(Entry).spec(Entry).snapshot)
    document = before.to_dict()
    document["declaration"]["properties"]["amount"]["yhmf"]["index_codec"] = "ordered-v999"
    changes = compare_schemas(before, SchemaSnapshot(canonical_json(document)))
    assert any(change.kind == "unsupported_schema" for change in changes)


class Label(StrEnum):
    LAST = "z"
    FIRST = "a"


class Rank(IntEnum):
    LAST = 10
    FIRST = -1


@pytest.mark.parametrize(
    "annotation,values",
    [
        (Label, [Label.FIRST, Label.LAST]),
        (Rank, [Rank.FIRST, Rank.LAST]),
        (Literal[2, -1], [-1, 2]),
    ],
)
def test_ordered_finite_choices_restore_their_declared_values(store, annotation, values):
    owner = create_model(
        "Choice",
        __base__=Model,
        id=(str, Field(primary_key=True)),
        value=(annotation, Field(ordered=True)),
    )
    owner.__global__ = "^YHMFTestProduct"
    db = Database(store, Registry(owner))
    for i, value in enumerate(reversed(values)):
        db.insert(owner(id=str(i), value=value))
    assert store.children(Path("^YHMFIndex", (b"YHMFTestProduct", b"value")), limit=100) == tuple(
        value_key(value) for value in values
    )
    assert db.query(owner).where(value=values[0]).all()[0].value == values[0]


def test_owned_ordered_index_uses_normal_patch_and_whole_save_transitions(store):
    class Child(Model):
        id: str = Field(primary_key=True)
        amount: Decimal = Field(ordered=True, unique=True)

    class Owner(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Owner))
    db.insert(Owner(id="A", children={"B": Child(id="B", amount=Decimal("1"))}))
    db.patch(Owner, "A", at=("children", "B"), changes={"amount": Decimal("2")})
    value = db.get(Owner, "A")
    value.children["B"].amount = Decimal("3")
    db.save(value)
    assert db.query(Owner).where(**{"children.amount": Decimal("3")}).occurrences()[0].at == (
        "children",
        "B",
    )
    db.delete_child(Owner, "A", at=("children", "B"))
    assert db.check_indexes(Owner).issues == ()


@pytest.mark.parametrize("existing_index", [False, True])
def test_ordered_index_activation_uses_existing_migration_paths(store, existing_index):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        amount: Decimal = Field(index=existing_index)

    class After(Before):
        amount: Decimal = Field(ordered=True, unique=True)

    old = Database(store, Registry(Before))
    old.insert(Before(id="A", amount=Decimal("12.500")))
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.execution_supported is not existing_index
    transform = (
        Transform("ordered.amount.v1", lambda old: After(id=old.id, amount=old.amount))
        if existing_index
        else None
    )
    plan = db.plan_migration(After, transform=transform)
    status = db.begin_migration(Before, After, plan=plan, transform=transform)
    for _ in range(40):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After, transform=transform)
    assert status.phase == "complete"
    assert db.get(After, "A").amount.as_tuple() == Decimal("12.500").as_tuple()
    assert db.query(After).where(amount=Decimal("12.5")).all()[0].id == "A"
    assert db.check_indexes(After).issues == ()


def test_ordered_addition_domain_failure_preserves_checkpoint_and_can_abort(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        amount: Decimal

    class After(Before):
        amount: Decimal = Field(ordered=True)

    old = Database(store, Registry(Before))
    old.insert(Before(id="A", amount=Decimal("1E30")))
    unchanged = dump(store, (Before.__global__, "^YHMFIndex", "^YHMFUnique"))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    with pytest.raises(StorageError, match="ordered-v1"):
        db.advance_migration(status.migration_id, Before, After)
    assert db.migration_status(status.migration_id) == status
    assert dump(store, (Before.__global__, "^YHMFIndex", "^YHMFUnique")) == unchanged
    assert db.abort_migration(status.migration_id).phase == "aborted"
    assert old.get(Before, "A").amount == Decimal("1E30")


def test_ordered_rebuild_review_and_recovery_keep_the_installed_codec(store):
    db = Database(store, Registry(Entry))
    db.insert(Entry(id="A", amount=Decimal("2.50")))
    definition = next(
        value for value in definitions(db.registry.spec(Entry)) if value.name == "amount"
    )
    store.delete_tree(index_root(db.registry.spec(Entry), definition, Decimal("2.5")))
    plan = db.plan_index_rebuild(Entry, fields=("amount",))
    assert plan.to_dict()["indexes"] == [{"field": "amount", "unique": True, "codec": "ordered-v1"}]
    before = dump(store, ("^YHMFSchema", "^YHMFIndex", "^YHMFMigration"))
    with pytest.raises(MigrationError, match="Reviewed"):
        db.begin_index_rebuild(Entry, plan=replace(plan, index_codecs=()))
    assert dump(store, ("^YHMFSchema", "^YHMFIndex", "^YHMFMigration")) == before
    status = db.begin_index_rebuild(Entry, plan=plan)
    for _ in range(40):
        if status.phase == "complete":
            break
        status = db.advance_index_rebuild(status.migration_id, Entry)
    assert status.phase == "complete" and db.check_indexes(Entry).issues == ()


@pytest.mark.native
def test_ordered_claims_and_nodes_restart_as_one_transaction(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")
    db = Database(store, Registry(Entry))
    db.insert(Entry(id="A", amount=Decimal("1")))
    attempts = []

    def change(tx):
        attempts.append(tx.get(Entry, "A").amount)
        tx.patch(Entry, "A", changes={"amount": Decimal("2.50")})
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(change)
    assert attempts == [Decimal("1"), Decimal("1")]
    assert db.query(Entry).where(amount=Decimal("2.5")).all()[0].id == "A"
    assert db.check_indexes(Entry).issues == ()
