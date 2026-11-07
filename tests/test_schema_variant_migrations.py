# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, Model, Registry


def test_nullable_decimal_revision_migration_preserves_storage_and_values(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        value: Decimal | None = Field(default=None, index=True)

    class After(Before):
        __schema_version__ = 2

    old = Database(store, Registry(Before))
    old.insert(Before(id="A", value=Decimal("12.50")))
    old.insert(Before(id="B"))
    path = old.registry.spec(Before).path("A").child(b"value")
    original = store.get(path)
    db = Database(store, Registry(After))
    plan = db.plan_migration(After)
    assert plan.execution_supported and plan.validation_only
    status = db.begin_migration(Before, After, plan=plan)
    for _ in range(30):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Before, After)
    assert status.phase == "complete"
    assert db.get(After, "A").value == Decimal("12.50")
    assert db.get(After, "B").value is None
    assert store.get(path) == original
    assert db.check_indexes(After).issues == ()
