# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, Inventory, Movement, Warehouse
from examples.inventory.migrations import (
    MOVEMENT_TIMESTAMPS,
    LegacyMovement,
    MovementV2,
    MovementV3,
)
from mccann_hub.yhmf import Database, Reference, Registry
from mccann_hub.yhmf.errors import SchemaMismatch
from tests.adversarial_support import dump
from tests.test_inventory_example import DOCK
from tests.test_inventory_timestamp_migration import legacy_database


def test_movement_timestamp_ordering_has_independent_golden():
    fixture = FilePath(__file__).with_name("fixtures") / "inventory-movement-ordered-v3.json"
    assert Registry(*MODELS).spec(Movement).snapshot == fixture.read_bytes().rstrip(b"\n")


@pytest.mark.parametrize("pause", ["backfill", "retag", "activate"])
def test_old_timestamp_transform_resumes_before_additive_ordering_upgrade(store, pause):
    stamps = ["2026-11-07T10:00:00+00:00", "2026-11-07T12:00:00+00:00"]
    legacy_database(store, stamps)
    models = tuple(model for model in MODELS if model is not Movement) + (MovementV2,)
    old = Database(store, Registry(*models))
    status = old.begin_migration(
        LegacyMovement,
        MovementV2,
        plan=old.plan_migration(MovementV2, transform=MOVEMENT_TIMESTAMPS),
        transform=MOVEMENT_TIMESTAMPS,
    )
    for _ in range(100):
        if status.phase == pause:
            break
        status = old.advance_migration(
            status.migration_id,
            LegacyMovement,
            MovementV2,
            transform=MOVEMENT_TIMESTAMPS,
            batch_size=1,
        )
    assert status.phase == pause
    for _ in range(100):
        if status.phase == "complete":
            break
        old = Database(store, Registry(*models))
        status = old.advance_migration(
            status.migration_id,
            LegacyMovement,
            MovementV2,
            transform=MOVEMENT_TIMESTAMPS,
            batch_size=1,
        )
    assert status.phase == "complete"
    before = dump(store, ("^InventoryMovement", "^YHMFRef", "^InventoryWarehouse"))
    db = Database(store, Registry(*MODELS))
    with pytest.raises(SchemaMismatch):
        db.get(Movement, "0")
    plan = db.plan_migration(Movement)
    assert plan.execution_supported
    status = db.begin_migration(MovementV2, Movement, plan=plan)
    for _ in range(100):
        if status.phase == "complete":
            break
        db = Database(store, Registry(*MODELS))
        status = db.advance_migration(status.migration_id, MovementV2, Movement, batch_size=1)
    assert status.phase == "complete"
    after = dump(store, ("^InventoryMovement", "^YHMFRef", "^InventoryWarehouse"))
    assert {path: value for path, value in before.items() if b"%m" not in path.subscripts} == {
        path: value for path, value in after.items() if b"%m" not in path.subscripts
    }
    query = db.query(Movement).order_by("occurred_at", direction="desc")
    first = query.page(limit=1)
    assert first.records[0].id == "1"
    assert [m.id for m in query.all(after=first.cursor)] == ["0"]
    assert db.check_indexes(Movement).issues == ()
    assert db.get(Warehouse, "MAIN").stored_on_hand == 2


def test_latest_inventory_movements_use_timestamp_then_reverse_key_ties(store):
    from examples.inventory import Lot
    from examples.inventory.__main__ import seed

    db = Database(store, Registry(*MODELS))
    seed(db)
    service = Inventory(db)
    for key, time in (("2", "10:00:00"), ("1", "12:00:00"), ("3", "12:00:00")):
        service.receive(key, "LOT-001", DOCK, Decimal(1), occurred_at=f"2026-11-07T{time}+00:00")
    timestamp = db.field(Movement, lambda m: m.occurred_at)
    query = (
        db.query(Movement)
        .where(lot=Reference[Lot](key="LOT-001"))
        .order_by(timestamp, direction="desc")
    )
    first = query.page(limit=2)
    assert [m.id for m in first.records] == ["3", "1"]
    assert [m.id for m in query.all(after=first.cursor)] == ["2"]
    assert query.explain().order_direction == "desc"
    assert query.explain().maximum_index_visits == 1000
    assert db.check_indexes(Movement).issues == ()


def test_frozen_movement_v3_preserves_the_original_golden_and_active_contract():
    fixture = FilePath(__file__).with_name("fixtures") / "inventory-movement-ordered-v3.json"
    models = tuple(model for model in MODELS if model is not Movement)
    frozen = Registry(*models, MovementV3).spec(MovementV3)
    assert frozen.snapshot == fixture.read_bytes().rstrip(b"\n")
    assert frozen.snapshot == Registry(*MODELS).spec(Movement).snapshot
