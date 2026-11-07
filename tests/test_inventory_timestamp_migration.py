# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import (
    MODELS,
    Balance,
    BinAddress,
    Inventory,
    Lot,
    Movement,
    Product,
    Warehouse,
)
from examples.inventory.__main__ import seed
from examples.inventory.migrations import (
    MOVEMENT_TIMESTAMPS_V3 as MOVEMENT_TIMESTAMPS,
)
from examples.inventory.migrations import (
    LegacyMovement,
    LegacyPosition,
    MovementV2,
)
from mccann_hub.yhmf import Database, Reference, Registry
from mccann_hub.yhmf.errors import MigrationError, SchemaMismatch
from tests.adversarial_support import dump


def legacy_database(store, stamps):
    models = tuple(model for model in MODELS if model is not Movement) + (LegacyMovement,)
    db = Database(store, Registry(*models))
    seed(db)
    db.insert_child(
        Warehouse,
        "MAIN",
        collection=("locations", "DOCK", "bins", "B1", "balances"),
        record=Balance(
            id="LOT-001",
            lot=Reference[Lot](key="LOT-001"),
            product=Reference[Product](key="WIDGET"),
            quantity=Decimal(len(stamps)),
        ),
    )
    for number, stamp in enumerate(stamps):
        db.insert(
            LegacyMovement(
                id=str(number),
                kind="receive",
                product=Reference[Product](key="WIDGET"),
                lot=Reference[Lot](key="LOT-001"),
                quantity=Decimal(1),
                destination=LegacyPosition(
                    warehouse=Reference[Warehouse](key="MAIN"), location="DOCK", bin="B1"
                ),
                reason="legacy receipt",
                occurred_at=stamp,
            )
        )
    return db


def finish(db, status):
    for _ in range(200):
        if status.phase == "complete":
            return status
        db = Database(db.store, Registry(*MODELS))
        status = db.advance_migration(
            status.migration_id,
            LegacyMovement,
            Movement,
            transform=MOVEMENT_TIMESTAMPS,
            batch_size=1,
        )
    raise AssertionError("Timestamp migration did not complete")


@pytest.mark.parametrize("phase", ["backfill", "retag", "activate"])
def test_ledger_timestamp_migration_is_explicit_resumable_and_preserves_stock(
    store, monkeypatch, phase
):
    old = legacy_database(store, ["2026-11-06T12:00:00+02:00", "2026-11-06T10:00:00Z"])
    db = Database(store, Registry(*MODELS))
    before_stock = dump(store, (Warehouse.__global__, Product.__global__, Lot.__global__))
    with pytest.raises(SchemaMismatch):
        db.get(Movement, "0")
    assert old.get(LegacyMovement, "0").occurred_at == "2026-11-06T12:00:00+02:00"
    status = db.begin_migration(
        LegacyMovement,
        Movement,
        plan=db.plan_migration(Movement, transform=MOVEMENT_TIMESTAMPS),
        transform=MOVEMENT_TIMESTAMPS,
    )
    for _ in range(100):
        if status.phase == phase:
            break
        status = db.advance_migration(
            status.migration_id,
            LegacyMovement,
            Movement,
            transform=MOVEMENT_TIMESTAMPS,
            batch_size=1,
        )
    assert status.phase == phase
    roots = tuple(model.__global__ for model in MODELS) + (
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFRef",
        "^YHMFUnique",
        "^YHMFMigration",
    )
    before, original = dump(store, roots), store.set

    def interrupt(path, value):
        original(path, value)
        raise RuntimeError("ledger timestamp interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", interrupt)
        with pytest.raises(RuntimeError):
            db.advance_migration(
                status.migration_id,
                LegacyMovement,
                Movement,
                transform=MOVEMENT_TIMESTAMPS,
                batch_size=1,
            )
    assert dump(store, roots) == before
    finish(db, status)
    assert dump(store, (Warehouse.__global__, Product.__global__, Lot.__global__)) == before_stock
    for number in range(2):
        value = db.get(Movement, str(number))
        assert value.occurred_at == datetime(2026, 11, 6, 10, tzinfo=UTC)
        assert value.occurred_at.tzinfo is UTC
    assert db.check_indexes(Movement).issues == ()
    Inventory(db).receive("new", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal(1))
    assert len(Inventory(db).history("LOT-001")) == 3


@pytest.mark.parametrize("stamp", ["invalid", "2026-11-06T12:00:00", "0001-01-01T00:00:00+01:00"])
def test_invalid_legacy_timestamp_rolls_back_and_allows_early_abort(store, stamp):
    old = legacy_database(store, [stamp])
    db = Database(store, Registry(*MODELS))
    status = db.begin_migration(
        LegacyMovement,
        Movement,
        plan=db.plan_migration(Movement, transform=MOVEMENT_TIMESTAMPS),
        transform=MOVEMENT_TIMESTAMPS,
    )
    with pytest.raises(MigrationError):
        db.advance_migration(
            status.migration_id, LegacyMovement, Movement, transform=MOVEMENT_TIMESTAMPS
        )
    assert db.migration_status(status.migration_id) == status
    db.abort_migration(status.migration_id)
    assert old.get(LegacyMovement, "0").occurred_at == stamp


def test_ledger_datetime_schema_has_a_separate_reviewed_golden():
    golden = FilePath(__file__).with_name("fixtures") / "inventory-movement-datetime-v2.json"
    models = tuple(model for model in MODELS if model is not Movement)
    assert Registry(*models, MovementV2).spec(MovementV2).snapshot == golden.read_bytes().rstrip(
        b"\n"
    )


def test_inventory_accepts_datetime_input_and_returns_a_utc_datetime(store):
    db = Database(store, Registry(*MODELS))
    seed(db)
    supplied = datetime(2026, 11, 6, 12, tzinfo=timezone(timedelta(hours=2)))
    movement = Inventory(db).receive(
        "new", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal(1), occurred_at=supplied
    )
    assert movement.occurred_at == supplied and movement.occurred_at.tzinfo is UTC
