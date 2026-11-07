# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal, localcontext

import pytest
from pydantic import computed_field

from mccann_hub.yhmf import Database, Field, Model, Registry
from mccann_hub.yhmf.errors import MutationError, QueryError, SchemaError
from tests.test_schema_contracts import seed_legacy


class Stored(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    amount: int = Field(default=2, index=True)


class Virtual(Stored):
    @computed_field
    @property
    def doubled(self) -> int:
        return self.amount * 2


def test_virtual_computation_is_outside_the_persistence_contract():
    assert Registry(Stored).spec(Stored).snapshot == Registry(Virtual).spec(Virtual).snapshot


@pytest.mark.parametrize("legacy", [False, True])
def test_virtual_values_follow_patch_and_save_without_stored_nodes(store, legacy):
    if legacy:
        seed_legacy(store, Stored(id=1))
    else:
        Database(store, Registry(Stored)).insert(Stored(id=1))
    db = Database(store, Registry(Virtual))
    original = db.inspect_schema(Virtual).installed
    value = db.get(Virtual, 1)
    assert value.doubled == 4
    assert value.model_dump()["doubled"] == 4
    db.patch(Virtual, 1, changes={"amount": 3})
    assert db.get(Virtual, 1).doubled == 6
    value.amount = 4
    db.save(value)
    assert db.get(Virtual, 1).doubled == 8
    assert db.inspect_schema(Virtual).installed == original
    assert db.check_indexes(Virtual).issues == ()
    root = db.registry.spec(Virtual).path(1)
    assert store.get(root.child(b"doubled")) is None
    with pytest.raises(MutationError):
        db.patch(Virtual, 1, changes={"doubled": 10})
    with pytest.raises(QueryError):
        db.query(Virtual).where(doubled=8)


def test_persistence_does_not_evaluate_virtual_properties(store):
    class Dangerous(Stored):
        @computed_field
        @property
        def external(self) -> int:
            raise AssertionError("Must not run during persistence")

    db = Database(store, Registry(Dangerous))
    db.insert(Dangerous(id=1))
    db.patch(Dangerous, 1, changes={"amount": 3})
    db.save(db.get(Dangerous, 1))


@pytest.mark.parametrize("flag", ["index", "unique", "primary_key", "stored"])
def test_virtual_fields_cannot_declare_persistence_metadata(flag):
    class Invalid(Stored):
        @computed_field(json_schema_extra={"yhmf": {flag: True}})
        @property
        def doubled(self) -> int:
            return self.amount * 2

    with pytest.raises(SchemaError, match="virtual"):
        Registry(Invalid)


def test_inventory_total_is_virtual_and_not_affected_by_callers_decimal_precision(store):
    from examples.inventory import MODELS, BinAddress, Inventory, Warehouse
    from examples.inventory.__main__ import seed

    db = Database(store, Registry(*MODELS))
    seed(db)
    inventory = Inventory(db)
    inventory.receive("R1", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal("123.456"))
    inventory.move(
        "M1",
        "LOT-001",
        BinAddress("MAIN", "DOCK", "B1"),
        BinAddress("MAIN", "STORAGE", "B1"),
        Decimal("23.456"),
    )
    with localcontext() as context:
        context.prec = 2
        assert db.get(Warehouse, "MAIN").total_on_hand == Decimal("123.456")
    assert db.check_indexes(Warehouse).issues == ()
