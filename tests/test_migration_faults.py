# SPDX-License-Identifier: GPL-3.0-or-later
"""Faults before installation and recovery after committed progress."""

from decimal import Decimal

import pytest

from mccann_hub.yhmf import Database, Registry
from mccann_hub.yhmf.maintenance import GATE
from mccann_hub.yhmf.schemas import schema_path
from tests.adversarial_support import dump
from tests.migration_models import Product, ProductV2

pytestmark = pytest.mark.adversarial
ROOTS = (
    "^YHMFTestProduct",
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
    "^YHMFMigration",
)


@pytest.mark.parametrize(
    "checkpoint", ["source_snapshot", "target_snapshot", "job", "gate", "barrier"]
)
def test_interrupted_job_installation_rolls_back_every_namespace(store, monkeypatch, checkpoint):
    Database(store, Registry(Product)).insert(
        Product(id="P", name="Main", sku="S", price=Decimal(1))
    )
    db = Database(store, Registry(ProductV2))
    plan = db.plan_migration(ProductV2)
    before, original = dump(store, ROOTS), store.set
    tripped = []

    def fail_after_set(path, value):
        original(path, value)
        matches = {
            "source_snapshot": path.subscripts[-1:] == (b"source_snapshot",),
            "target_snapshot": path.subscripts[-1:] == (b"target_snapshot",),
            "job": path.global_name == "^YHMFMigration" and len(path.subscripts) == 1,
            "gate": path == GATE,
            "barrier": path == schema_path(Product.__global__),
        }
        if matches[checkpoint]:
            tripped.append(path)
            raise RuntimeError("injected installation interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", fail_after_set)
        with pytest.raises(RuntimeError, match="injected"):
            db.begin_migration(Product, ProductV2, plan=plan)
    assert tripped
    assert dump(store, ROOTS) == before
    status = db.begin_migration(Product, ProductV2, plan=plan)
    db.abort_migration(status.migration_id)
    assert Database(store, Registry(Product)).get(Product, "P").sku == "S"


@pytest.mark.parametrize("interruption", ["validate", "backfill", "verify", "retag", "activate"])
def test_committed_progress_survives_reopening_at_every_migration_phase(store, interruption):
    old = Database(store, Registry(Product))
    for index in range(3):
        old.insert(Product(id=str(index), name="Main", sku=str(index), price=Decimal("1.2300")))
    db = Database(store, Registry(ProductV2))
    status = db.begin_migration(Product, ProductV2, plan=db.plan_migration(ProductV2))
    for _ in range(100):
        if status.phase == interruption:
            break
        status = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    assert status.phase == interruption
    status = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    saved = status
    db = Database(store, Registry(ProductV2))
    assert db.migration_status(status.migration_id) == saved
    for _ in range(100):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Product, ProductV2, batch_size=1)
    assert status.phase == "complete"
    before = dump(store, ROOTS)
    assert db.advance_migration(status.migration_id, Product, ProductV2) == status
    assert dump(store, ROOTS) == before
    assert db.check_indexes(ProductV2).issues == ()
    assert all(
        value.price.as_tuple() == Decimal("1.2300").as_tuple() for value in db.scan(ProductV2)
    )
