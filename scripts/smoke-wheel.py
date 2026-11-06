# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with an isolated Python from an environment containing the built wheel."""

from decimal import Decimal
from importlib.metadata import version

import mccann_hub
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Registry, __version__
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


def finish(status, advance):
    for _ in range(200):
        if status.phase == "complete":
            return status
        status = advance(status.migration_id)
    raise AssertionError("Maintenance job did not complete")


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
    print("Installed wheel: schema migration, index rebuild, and namespace checks passed")


if __name__ == "__main__":
    main()
