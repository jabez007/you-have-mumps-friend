# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path as FilePath
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mccann_hub.yhmf import Database, Field, Model, Registry
from mccann_hub.yhmf.codecs import decode_scalar, encode_scalar
from mccann_hub.yhmf.errors import SchemaError, StorageError, UniqueViolation

VALUE = UUID("12345678-1234-5678-9abc-def012345678")


class Entity(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    token: UUID | None = Field(default=None, unique=True)


def test_uuid_golden_uses_canonical_lowercase_hyphenated_bytes():
    assert encode_scalar(VALUE) == b"u:12345678-1234-5678-9abc-def012345678"
    assert decode_scalar(b"u:12345678-1234-5678-9abc-def012345678") == VALUE
    from mccann_hub.yhmf.codecs import canonical_json

    fixture = json.loads((FilePath(__file__).with_name("fixtures") / "uuid-v1.json").read_text())
    assert Registry(Entity).spec(Entity).snapshot == canonical_json(fixture["schema"])
    for value in fixture["values"]:
        assert encode_scalar(UUID(value["input"])) == value["encoded"].encode("ascii")


@given(st.uuids())
def test_uuid_roundtrip(value):
    assert decode_scalar(encode_scalar(value)) == value


@pytest.mark.parametrize(
    "raw",
    [
        b"u:12345678123456789abcdef012345678",
        b"u:12345678-1234-5678-9ABC-DEF012345678",
        b"u:{12345678-1234-5678-9abc-def012345678}",
        b"u:invalid",
    ],
)
def test_noncanonical_uuid_storage_is_rejected(raw):
    with pytest.raises(StorageError):
        decode_scalar(raw)


def test_uuid_fields_patch_and_query_with_atomic_unique_claims(store):
    db = Database(store, Registry(Entity))
    db.insert(Entity(id="A", token=VALUE))
    assert db.get(Entity, "A").token == VALUE
    assert [row.id for row in db.query(Entity).where(token=VALUE).all()] == ["A"]
    with pytest.raises(UniqueViolation):
        db.insert(Entity(id="B", token=VALUE))
    db.patch(Entity, "A", changes={"token": None})
    db.insert(Entity(id="B", token=VALUE))
    assert db.check_indexes(Entity).issues == ()


def test_uuid_literal_default_migration_uses_the_existing_executor(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class After(Before):
        token: UUID = Field(default=VALUE, index=True)

    Database(store, Registry(Before)).insert(Before(id="A"))
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.execution_supported
    status = db.begin_migration(Before, After, plan=plan)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete" and db.get(After, "A").token == VALUE
    assert db.query(After).where(token=VALUE).all()[0].id == "A"


def test_uuid_primary_keys_still_require_a_separate_identity_design():
    class Invalid(Model):
        __global__ = "^YHMFTestProduct"
        id: UUID = Field(primary_key=True)

    with pytest.raises(SchemaError, match="Primary keys"):
        Registry(Invalid)
