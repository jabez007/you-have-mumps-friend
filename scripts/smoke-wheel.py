# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with an isolated Python from an environment containing the built wheel."""

from decimal import Decimal
from importlib.metadata import version

import mccann_hub
from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    Patch,
    Path,
    Registry,
    Transform,
    __version__,
)
from mccann_hub.yhmf.errors import UniqueViolation


class Before(Model):
    __global__ = "^YHMFWheelProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, index=True)
    price: Decimal


class After(Before):
    __schema_version__ = 2
    sku: str | None = Field(default=None, unique=True)
    price: Decimal = Field(unique=True)
    note: str | None = Field(default=None, index=True)


class WheelLot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0, index=True)


class WheelWarehouse(Model):
    __global__ = "^YHMFWheelWarehouse"
    id: str = Field(primary_key=True)
    lots: Children[WheelLot] = ChildField(key="id")


def smoke_mutations(store):
    db = Database(store, Registry(WheelWarehouse))
    db.insert(WheelWarehouse(id="W", lots={"old": WheelLot(id="old")}))

    def receive(tx):
        tx.insert_child(WheelWarehouse, "W", collection=("lots",), record=WheelLot(id="new"))
        tx.patch(WheelWarehouse, "W", at=("lots", "new"), changes={"quantity": Decimal("2.5")})
        tx.delete_child(WheelWarehouse, "W", at=("lots", "old"))

    db.atomic(receive)
    path = db.path(WheelWarehouse, lambda w: w.lots["new"])
    owned = db.get(WheelWarehouse, "W", at=path)
    assert isinstance(owned, WheelLot) and owned.quantity == Decimal("2.5")
    assert db.get(WheelWarehouse, "W").lots.keys() == {"new"}
    assert db.check_indexes(WheelWarehouse).issues == ()


def finish(status, advance):
    for _ in range(200):
        if status.phase == "complete":
            return status
        status = advance(status.migration_id)
    raise AssertionError("Maintenance job did not complete")


def smoke_transform(store):
    class Converted(Model):
        __global__ = Before.__global__
        __schema_version__ = 3
        id: str = Field(primary_key=True)
        sku: str | None = Field(default=None, unique=True)
        cost: str = Field(unique=True)
        note: str | None = Field(default=None, index=True)

    def convert(record):
        return Converted(id=record.id, sku=record.sku, cost=str(record.price), note=record.note)

    transform = Transform("wheel.price-to-cost.v1", convert)
    db = Database(store, Registry(Converted))
    status = db.begin_migration(
        After,
        Converted,
        plan=db.plan_migration(Converted, transform=transform),
        transform=transform,
    )
    finish(
        status,
        lambda job: db.advance_migration(job, After, Converted, transform=transform, batch_size=1),
    )
    assert db.get(Converted, "0").cost == "2.00"
    assert db.check_indexes(Converted).issues == ()


def main():
    assert mccann_hub.__file__ is None
    assert version("mccann-hub-yhmf") == __version__
    store = MemoryStore()
    old = Database(store, Registry(Before))
    for number, sku in enumerate(("one", None, None)):
        old.insert(Before(id=str(number), sku=sku, price=Decimal(f"{number + 1}.00")))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    finish(status, lambda job_id: db.advance_migration(job_id, Before, After, batch_size=1))
    assert db.get(After, "0").price.as_tuple() == Decimal("1.00").as_tuple()
    fingerprint = db.inspect_schema(After).installed.fingerprint
    for name in ("^YHMFIndex", "^YHMFUnique"):
        store.delete_tree(Path(name, (b"YHMFWheelProduct",)))
    store.set(Path("^YHMFIndex", (b"YHMFWheelProduct", b"sku", b"orphan")), b"stale")
    status = db.begin_index_rebuild(After, plan=db.plan_index_rebuild(After))
    status = finish(status, lambda job_id: db.advance_index_rebuild(job_id, After, batch_size=1))
    assert status.verified == 3 and status.retagged == 0
    assert db.inspect_schema(After).installed.fingerprint == fingerprint
    assert db.check_indexes(After).issues == ()
    assert len(db.query(After).where(sku=None).all()) == 2
    assert db.query(After).where(price=Decimal("1.0")).all()[0].id == "0"
    try:
        db.insert(After(id="duplicate", sku="one", price=Decimal(4)))
    except UniqueViolation:
        pass
    else:
        raise AssertionError("Rebuilt uniqueness constraint was not enforced")
    smoke_mutations(store)
    product = db.registry.reference(After, lambda p: p.id)
    assert db.resolve(product("0")).price == Decimal("1.00")
    db.patch_many(
        [
            Patch(After, "0", changes={"price": Decimal("2.00")}),
            Patch(After, "1", changes={"price": Decimal("1.00")}),
        ]
    )
    assert db.get(After, "0").price == Decimal("2.00")
    assert db.check_indexes(After).issues == ()
    smoke_transform(store)
    print(
        "Installed wheel: migration, rebuild, hierarchical mutations, and namespace checks passed"
    )


if __name__ == "__main__":
    main()
