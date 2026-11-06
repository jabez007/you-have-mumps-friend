# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
from datetime import date
from pathlib import Path

import pytest
from pydantic import field_validator

from mccann_hub.yhmf import Field, Model, Registry
from mccann_hub.yhmf.canonical_schema import schema_bytes
from mccann_hub.yhmf.errors import SchemaError
from mccann_hub.yhmf.registry import _node_spec
from tests.canonical_models import Warehouse


def canonical(model):
    return schema_bytes(_node_spec(model, keyed=True, ancestors=()), model.__global__)


def test_canonical_golden_is_independent_of_pydantic_output():
    fixture = Path(__file__).with_name("fixtures") / "schema-canonical-v2.json"
    assert canonical(Warehouse) == fixture.read_bytes()


def test_titles_descriptions_class_names_and_field_order_do_not_define_persistence():
    class First(Model):
        __global__ = "^Example"
        id: str = Field(primary_key=True, description="Before")
        value: int = Field(default=4, title="Before")

    class Renamed(Model):
        __global__ = "^Example"
        value: int = Field(default=4, title="After")
        id: str = Field(primary_key=True, description="After")

        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            raise AssertionError("Canonical generation must not use JSON Schema")

    assert canonical(First) == canonical(Renamed)


@pytest.mark.parametrize("change", ["type", "default", "constraint", "index", "version", "policy"])
def test_persistence_changes_have_distinct_fingerprints(change):
    from pydantic import ConfigDict, create_model

    base = create_model("Base", __base__=Model, id=(str, Field(primary_key=True)), value=(int, 1))
    base.__global__ = "^Example"
    declaration = {
        "type": (str, "1"),
        "default": (int, 2),
        "constraint": (int, Field(default=1, ge=0)),
        "index": (int, Field(default=1, index=True)),
    }.get(change, (int, 1))
    other = create_model(
        "Other", __base__=Model, id=(str, Field(primary_key=True)), value=declaration
    )
    other.__global__ = "^Example"
    if change == "version":
        other.__schema_version__ = 2
    if change == "policy":
        other.model_config = ConfigDict(**{**other.model_config, "strict": False})
    assert hashlib.sha256(canonical(base)).digest() != hashlib.sha256(canonical(other)).digest()


def test_callable_policy_requires_an_application_revision_and_never_runs_factories():
    def forbidden():
        raise AssertionError("Factory must never execute during registration")

    class Custom(Model):
        __global__ = "^Example"
        id: str = Field(primary_key=True)
        value: int = Field(default_factory=forbidden)

        @field_validator("value")
        @classmethod
        def positive(cls, value):
            return abs(value)

    before = canonical(Custom)
    assert b'"custom_validation":true' in before
    assert b'"default_factory":true' in before
    Custom.__schema_version__ = 2
    assert canonical(Custom) != before


def test_dates_and_compound_keys_are_not_newly_supported():
    class Unsupported(Model):
        __global__ = "^Example"
        id: str = Field(primary_key=True)
        when: date

    with pytest.raises(SchemaError, match="Unsupported field"):
        Registry(Unsupported)

    class Compound(Model):
        __global__ = "^Example"
        id: tuple[str, int] = Field(primary_key=True)

    with pytest.raises(SchemaError, match="Primary keys"):
        Registry(Compound)
