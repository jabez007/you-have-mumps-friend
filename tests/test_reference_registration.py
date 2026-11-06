# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Field, Model, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation, SchemaError


class Target(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)


class Revised(Target):
    __schema_version__ = 2
    note: str = ""


class Source(Model):
    __global__ = "^YHMFTestOrder"
    id: int = Field(primary_key=True)
    target: Reference[Target]


def test_unregistered_reference_names_the_source_field_and_target():
    with pytest.raises(SchemaError) as error:
        Registry(Revised, Source)
    assert "Source.target" in str(error.value)
    assert "Target" in str(error.value)
    assert "redeclare" in str(error.value)


def test_rebound_referrer_requires_no_migration_when_target_identity_is_unchanged(store):
    class Rebound(Source):
        target: Reference[Revised]

    old = Database(store, Registry(Target, Source))
    old.insert(Target(id=1))
    old.insert(Source(id=1, target=Reference[Target](key=1)))
    db = Database(store, Registry(Revised, Rebound))
    assert db.inspect_schema(Rebound).status == "matching"
    assert Registry(Source, Target).spec(Source).snapshot == db.registry.spec(Rebound).snapshot
    status = db.begin_migration(Target, Revised, plan=db.plan_migration(Revised))
    for _ in range(100):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Target, Revised, batch_size=1)
    assert status.phase == "complete"
    assert db.get(Rebound, 1).target == Reference[Revised](key=1)
    assert db.plan_migration(Rebound).is_noop
    with pytest.raises(ReferenceViolation):
        db.delete(Revised, 1)
    assert db.delete(Rebound, 1)
    assert db.delete(Revised, 1)
