# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry policy rejects silent loss without fingerprinting transient options."""

from decimal import Decimal

import pytest
from pydantic import ConfigDict, ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import SchemaError, StorageError


class Checked(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    value: int = Field(default=1, ge=0)


@pytest.mark.parametrize(
    "ownership", ["root", "embedded", "children", "nullable_embedded", "nullable_children"]
)
def test_extra_allow_is_rejected_for_every_persisted_owner(ownership):
    class Loose(Model):
        model_config = ConfigDict(extra="allow")
        id: str = Field(primary_key=True)
        value: int = 1

    class Root(Loose):
        __global__ = "^YHMFTestProduct"

    class Embedded(Checked):
        item: Loose

    class Owned(Checked):
        items: Children[Loose] = ChildField(key="id")

    class NullableEmbedded(Checked):
        item: Loose | None = None

    class NullableOwned(Checked):
        items: Children[Loose] | None = ChildField(None, key="id")

    model = {
        "root": Root,
        "embedded": Embedded,
        "children": Owned,
        "nullable_embedded": NullableEmbedded,
        "nullable_children": NullableOwned,
    }[ownership]
    with pytest.raises(SchemaError, match="extra.*allow"):
        Registry(model)


@pytest.mark.parametrize(
    "config",
    [
        {"revalidate_instances": "never"},
        {"revalidate_instances": "subclass-instances"},
        {"validate_default": False},
        {"validate_assignment": False},
        {"allow_inf_nan": True},
        {"extra": "ignore"},
    ],
)
def test_safe_configuration_overrides_preserve_the_installed_contract_and_validate_writes(
    store, config
):
    configured = type("Configured", (Checked,), {"model_config": ConfigDict(**config)})
    original = Registry(Checked).spec(Checked)
    assert Registry(configured).spec(configured).snapshot == original.snapshot
    db = Database(store, Registry(Checked))
    db.insert(Checked(id="P"))
    current = Database(store, Registry(configured))
    assert current.get(configured, "P").value == 1
    invalid = configured.model_construct(id="P", value=-1)
    with pytest.raises(ValidationError):
        current.save(invalid)
    assert db.get(Checked, "P").value == 1
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) == original.fingerprint


@pytest.mark.parametrize("ownership", ["root", "embedded", "children"])
def test_disabled_default_and_instance_validation_cannot_persist_invalid_values(store, ownership):
    class Unchecked(Model):
        model_config = ConfigDict(
            validate_default=False, revalidate_instances="never", validate_assignment=False
        )
        id: str = Field(primary_key=True)
        value: int = Field(default=-1, ge=0)

    class Root(Unchecked):
        __global__ = "^YHMFTestProduct"

    class Embedded(Checked):
        item: Unchecked

    class Owned(Checked):
        items: Children[Unchecked] = ChildField(key="id")

    records = {
        "root": Root(id="P"),
        "embedded": Embedded(id="P", item=Unchecked(id="C")),
        "children": Owned(id="P", items={"C": Unchecked(id="C")}),
    }
    record = records[ownership]
    db = Database(store, Registry(type(record)))
    with pytest.raises(ValidationError):
        db.insert(record)
    assert store.children(Path("^YHMFTestProduct"), limit=1) == ()
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None


@pytest.mark.parametrize(
    "kind,value",
    [
        (float, float("inf")),
        (float, float("-inf")),
        (float, float("nan")),
        (Decimal, Decimal("NaN")),
        (Decimal, Decimal("Infinity")),
    ],
)
def test_allow_inf_nan_never_bypasses_finite_storage_codecs(store, kind, value):
    from pydantic import create_model

    model = create_model(
        "Nonfinite",
        __base__=Model,
        __config__=ConfigDict(allow_inf_nan=True),
        id=(str, Field(primary_key=True)),
        value=(kind, ...),
    )
    model.__global__ = "^YHMFTestProduct"
    record = model(id="P", value=value)
    db = Database(store, Registry(model))
    with pytest.raises(StorageError, match="nonfinite"):
        db.insert(record)
    assert store.children(Path("^YHMFTestProduct"), limit=1) == ()
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None
