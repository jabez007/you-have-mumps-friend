# SPDX-License-Identifier: GPL-3.0-or-later
"""Movement choice revisions never retarget historical jobs or rewrite ledger values."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import MODELS, Lot, Movement, Product, Warehouse
from examples.inventory.__main__ import seed
from examples.inventory.migrations import LegacyPosition, MovementV3
from mccann_hub.yhmf import Database, Reference, Registry
from mccann_hub.yhmf.errors import SchemaMismatch
from tests.adversarial_support import dump


def test_current_movement_kind_contract_is_explicit_revision_four():
    assert Movement.__schema_version__ == 4


@pytest.mark.parametrize("pause", ["validate", "backfill", "retag", "activate"])
def test_v3_choices_upgrade_is_explicit_resumable_and_preserves_record_bytes(store, pause):
    from examples.inventory.migrations import MOVEMENT_KINDS_V4

    old_models = tuple(model for model in MODELS if model is not Movement) + (MovementV3,)
    old = Database(store, Registry(*old_models))
    seed(old)
    old.insert(
        MovementV3(
            id="OLD",
            kind="receive",
            product=Reference[Product](key="WIDGET"),
            lot=Reference[Lot](key="LOT-001"),
            quantity=Decimal(1),
            destination=LegacyPosition(
                warehouse=Reference[Warehouse](key="MAIN"), location="DOCK", bin="B1"
            ),
            reason="legacy",
            occurred_at=datetime(2026, 11, 7, tzinfo=UTC),
        )
    )
    roots = tuple(model.__global__ for model in MODELS) + ("^YHMFIndex", "^YHMFUnique", "^YHMFRef")
    before = dump(store, roots)
    db = Database(store, Registry(*MODELS))
    with pytest.raises(SchemaMismatch):
        db.get(Movement, "OLD")
    assert not db.plan_migration(Movement).execution_supported
    plan = db.plan_migration(Movement, transform=MOVEMENT_KINDS_V4)
    assert plan.execution_supported
    job = db.begin_migration(MovementV3, Movement, plan=plan, transform=MOVEMENT_KINDS_V4)
    for _ in range(100):
        if job.phase == pause:
            break
        job = db.advance_migration(
            job.migration_id, MovementV3, Movement, transform=MOVEMENT_KINDS_V4, batch_size=1
        )
    assert job.phase == pause
    for _ in range(100):
        if job.phase == "complete":
            break
        db = Database(store, Registry(*MODELS))
        job = db.advance_migration(
            job.migration_id, MovementV3, Movement, transform=MOVEMENT_KINDS_V4, batch_size=1
        )
    assert job.phase == "complete"
    after = dump(store, roots)
    assert {p: v for p, v in before.items() if b"%m" not in p.subscripts} == {
        p: v for p, v in after.items() if b"%m" not in p.subscripts
    }
    assert db.get(Movement, "OLD").kind == "receive"
    assert db.check_indexes(Movement).issues == ()
    assert "inserttx(input)" in db.m_routine(Movement, name="MovementAPI")


def test_movement_choice_schema_has_a_separate_golden():
    fixture = FilePath(__file__).with_name("fixtures") / "inventory-movement-choices-v4.json"
    assert Registry(*MODELS).spec(Movement).snapshot == fixture.read_bytes().rstrip(b"\n")


@pytest.mark.parametrize("pause", ["backfill", "retag", "activate"])
def test_original_v1_to_v3_job_finishes_with_frozen_bindings_before_kinds_v4(store, pause):
    from examples.inventory.migrations import (
        MOVEMENT_KINDS_V4,
        MOVEMENT_TIMESTAMPS_V3,
        LegacyMovement,
    )
    from tests.test_inventory_timestamp_migration import legacy_database

    legacy_database(store, ["2026-11-07T10:00:00Z"])
    models3 = tuple(model for model in MODELS if model is not Movement) + (MovementV3,)
    db3 = Database(store, Registry(*models3))
    job = db3.begin_migration(
        LegacyMovement,
        MovementV3,
        plan=db3.plan_migration(MovementV3, transform=MOVEMENT_TIMESTAMPS_V3),
        transform=MOVEMENT_TIMESTAMPS_V3,
    )
    for _ in range(100):
        if job.phase == pause:
            break
        job = db3.advance_migration(
            job.migration_id,
            LegacyMovement,
            MovementV3,
            transform=MOVEMENT_TIMESTAMPS_V3,
            batch_size=1,
        )
    assert job.phase == pause
    for _ in range(100):
        if job.phase == "complete":
            break
        db3 = Database(store, Registry(*models3))
        job = db3.advance_migration(
            job.migration_id,
            LegacyMovement,
            MovementV3,
            transform=MOVEMENT_TIMESTAMPS_V3,
            batch_size=1,
        )
    assert job.phase == "complete"
    assert db3.get(MovementV3, "0").kind == "receive"
    db = Database(store, Registry(*MODELS))
    job = db.begin_migration(
        MovementV3,
        Movement,
        plan=db.plan_migration(Movement, transform=MOVEMENT_KINDS_V4),
        transform=MOVEMENT_KINDS_V4,
    )
    for _ in range(100):
        if job.phase == "complete":
            break
        job = db.advance_migration(
            job.migration_id, MovementV3, Movement, transform=MOVEMENT_KINDS_V4, batch_size=1
        )
    assert job.phase == "complete"
    assert db.get(Movement, "0").occurred_at == datetime(2026, 11, 7, 10, tzinfo=UTC)
