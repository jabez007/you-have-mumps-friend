# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Annotated

import pytest
from pydantic import AfterValidator

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Registry
from mccann_hub.yhmf import registry as declarations
from mccann_hub.yhmf.errors import SchemaError


def test_operations_reuse_registered_guards_and_field_adapters(store, monkeypatch):
    class Child(Model):
        id: int = Field(primary_key=True)
        value: str = ""

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        name: str = Field(default="original", index=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Root))
    db.insert(Root(id=1, children={1: Child(id=1)}))

    def recompiled(*args, **kwargs):
        raise AssertionError("Declaration rules recompiled during database work")

    monkeypatch.setattr(declarations, "supports_local_validation", recompiled)
    monkeypatch.setattr(declarations, "field_adapter", recompiled)
    monkeypatch.setattr(declarations, "TypeAdapter", recompiled)
    db.patch(Root, 1, changes={"name": "updated"})
    db.patch(Root, 1, at=("children", 1), changes={"value": "updated"})
    db.insert_child(Root, 1, collection=("children",), record=Child(id=2))
    assert [row.id for row in db.query(Root).where(name="updated").all()] == [1]
    assert [row.id for row in db.query(Root).where(id=1).all()] == [1]


def test_equal_fingerprints_do_not_share_custom_adapter_behavior():
    from mccann_hub.yhmf import MemoryStore

    def declaration(normalize):
        class Root(Model):
            __global__ = "^YHMFTestWarehouse"
            id: int = Field(primary_key=True)
            name: Annotated[str, AfterValidator(normalize)] = Field(index=True)

        return Root

    upper, lower = declaration(str.upper), declaration(str.lower)
    first, second = Registry(upper), Registry(lower)
    assert first.spec(upper).fingerprint == second.spec(lower).fingerprint
    for model, registry, expected in ((upper, first, "MIXED"), (lower, second, "mixed")):
        db = Database(MemoryStore(), registry)
        db.insert(model(id=1, name="MiXeD"))
        assert db.get(model, 1).name == expected
        assert [row.id for row in db.query(model).where(name="MiXeD").all()] == [1]


@pytest.mark.parametrize("rebuilt", ["root", "child"])
def test_rebuilt_declarations_require_a_fresh_registry(store, rebuilt):
    class Child(Model):
        id: int = Field(primary_key=True)

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Root))
    db.insert(Root(id=1, children={1: Child(id=1)}))
    (Root if rebuilt == "root" else Child).model_rebuild(force=True)
    with pytest.raises(SchemaError, match="rebuilt after registration"):
        db.get(Root, 1)
    assert Database(store, Registry(Root)).get(Root, 1).id == 1


def test_config_changes_cannot_silently_reuse_compiled_rules(store):
    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        name: str = Field(index=True)

    db = Database(store, Registry(Root))
    db.insert(Root(id=1, name="UPPER"))
    Root.model_config["str_to_lower"] = True
    with pytest.raises(SchemaError, match="changed.*after registration"):
        db.query(Root).where(name="UPPER")


@pytest.mark.parametrize("owner", ["root", "child"])
@pytest.mark.parametrize(
    "attribute,value",
    [
        ("__schema_version__", 2),
        ("__schema_version__", True),
        ("__schema_version__", 1.0),
        ("__global__", "^YHMFTestProduct"),
    ],
)
@pytest.mark.parametrize("installed", [False, True])
def test_registered_storage_identity_changes_cannot_write_stale_metadata(
    store, owner, attribute, value, installed
):
    from tests.adversarial_support import dump

    class Child(Model):
        id: int = Field(primary_key=True)

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        children: Children[Child] = ChildField(key="id")

    db = Database(store, Registry(Root))
    if installed:
        db.insert(Root(id=1))
    before = dump(store, ("^YHMFTestWarehouse", "^YHMFTestProduct", "^YHMFSchema"))
    setattr(Root if owner == "root" else Child, attribute, value)
    with pytest.raises(SchemaError, match="changed.*after registration"):
        db.insert(Root(id=2, children={1: Child(id=1)}))
    assert dump(store, ("^YHMFTestWarehouse", "^YHMFTestProduct", "^YHMFSchema")) == before
