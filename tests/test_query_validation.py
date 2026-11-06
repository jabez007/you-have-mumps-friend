# SPDX-License-Identifier: GPL-3.0-or-later
"""Equality predicates use the same declarative field policy as stored values."""

import pytest
from pydantic import ConfigDict, ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Reference, Registry


@pytest.mark.parametrize(
    "config,value,expected",
    [
        ({"str_to_lower": True}, "Me@X.org", "me@x.org"),
        ({"str_to_upper": True}, "Me@X.org", "ME@X.ORG"),
        ({"str_strip_whitespace": True}, " Me@X.org ", "Me@X.org"),
        ({"str_strip_whitespace": True, "str_to_lower": True}, " Me@X.org ", "me@x.org"),
    ],
)
def test_string_query_normalization_matches_its_owning_model(store, config, value, expected):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(**config)
        id: int = Field(primary_key=True)
        value: str = Field(index=True)

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, value=value))
    assert db.get(Row, 1).value == expected
    assert [row.id for row in db.query(Row).where(value=value).all()] == [1]
    assert [row.id for row in db.query(Row).where(value=expected).all()] == [1]
    assert db.check_indexes(Row).issues == ()


@pytest.mark.parametrize(
    "config,bad", [({"str_min_length": 3}, "ab"), ({"str_max_length": 3}, "abcd")]
)
def test_query_values_enforce_owner_string_length_settings(store, config, bad):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(**config)
        id: int = Field(primary_key=True)
        value: str = Field(index=True)

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, value="abc"))
    with pytest.raises(ValidationError):
        db.query(Row).where(value=bad)


@pytest.mark.parametrize("model_strict,field_strict", [(False, None), (True, False)])
def test_query_does_not_override_declared_nonstrict_validation(store, model_strict, field_strict):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(strict=model_strict)
        id: int = Field(primary_key=True)
        value: int = Field(index=True, strict=field_strict)

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, value="2"))
    assert [row.id for row in db.query(Row).where(value="2").all()] == [1]


def test_explicit_field_strictness_still_overrides_a_nonstrict_owner(store):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(strict=False)
        id: int = Field(primary_key=True)
        value: int = Field(index=True, strict=True)

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, value=2))
    with pytest.raises(ValidationError):
        db.query(Row).where(value="2")


def test_nested_index_uses_child_policy_instead_of_root_policy(store):
    class Child(Model):
        model_config = ConfigDict(str_to_lower=True, str_strip_whitespace=True)
        id: int = Field(primary_key=True)
        value: str = Field(index=True)

    class Root(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(str_to_upper=True)
        id: int = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Root))
    db.insert(Root(id=1, children={1: Child(id=1, value=" MiXeD ")}))
    assert [row.id for row in db.query(Root).where(**{"children.value": " MiXeD "}).all()] == [1]


def test_primary_key_predicate_uses_the_declared_normalization(store):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(str_to_lower=True, str_strip_whitespace=True)
        id: str = Field(primary_key=True)

    db = Database(store, Registry(Row))
    db.insert(Row(id=" KEY "))
    query = db.query(Row).where(id=" KEY ")
    assert query.explain().strategy == "direct_key"
    assert [row.id for row in query.all()] == ["key"]


def test_reference_and_nullable_predicates_keep_the_reference_models_config(store):
    class Target(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)

    class Source(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(str_to_lower=True, str_strip_whitespace=True)
        id: int = Field(primary_key=True)
        target: Reference[Target] | None = Field(default=None, index=True)

    db = Database(store, Registry(Target, Source))
    db.insert(Target(id="KEEPCASE"))
    value = Reference[Target](key="KEEPCASE")
    db.insert(Source(id=1, target=value))
    db.insert(Source(id=2))
    assert [row.id for row in db.query(Source).where(target=value).all()] == [1]
    assert [row.id for row in db.query(Source).where(target=None).all()] == [2]


def test_query_uses_the_owner_regex_engine(store):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        model_config = ConfigDict(regex_engine="python-re")
        id: int = Field(primary_key=True)
        value: str = Field(index=True, pattern=r"(?<=x)y")

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, value="xy"))
    assert [row.id for row in db.query(Row).where(value="xy").all()] == [1]
    with pytest.raises(ValidationError):
        db.query(Row).where(value="xz")
