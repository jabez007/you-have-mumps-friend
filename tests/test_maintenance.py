# SPDX-License-Identifier: GPL-3.0-or-later
import json
from dataclasses import replace

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry
from mccann_hub.yhmf.codecs import canonical_json
from mccann_hub.yhmf.errors import MaintenanceActive, MigrationError, StorageError
from mccann_hub.yhmf.maintenance import (
    GATE,
    MigrationStatus,
    job_path,
    load_status,
    marker,
    require_owner,
    save_status,
)
from mccann_hub.yhmf.schemas import schema_path


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str = Field(index=True)


def gated(store):
    registry = Registry(Product)
    db = Database(store, registry)
    db.insert(Product(id="private-key", name="Main"))
    spec = registry.spec(Product)
    status = MigrationStatus("a" * 32, spec.global_name, spec.fingerprint.decode(), "b" * 64)

    def claim():
        save_status(store, status)
        store.set(GATE, status.migration_id.encode())
        store.set(schema_path(spec.global_name), marker(status.migration_id))

    store.atomic(claim)
    return db, status


def test_checkpoint_roundtrip_and_private_cursor(store):
    _, status = gated(store)
    status = replace(status, validated=1, cursor=b"s:private-key")
    store.atomic(lambda: save_status(store, status))
    assert store.atomic(lambda: load_status(store, status.migration_id)) == status
    assert "private-key" not in repr(status)
    assert "cursor" not in status.to_dict()
    assert status.to_dict(include_cursor=True)["cursor"] == status.cursor.hex()
    store.atomic(lambda: require_owner(store, status))


@pytest.mark.parametrize("operation", ["get", "save", "delete", "scan", "query", "audit"])
def test_ordinary_access_observes_gate(store, operation):
    db, _ = gated(store)
    operations = {
        "get": lambda: db.get(Product, "private-key"),
        "save": lambda: db.save(Product(id="another", name="New")),
        "delete": lambda: db.delete(Product, "private-key"),
        "scan": lambda: db.scan(Product),
        "query": lambda: db.query(Product).where(name="Main").all(),
        "audit": lambda: db.check_indexes(Product),
    }
    with pytest.raises(MaintenanceActive):
        operations[operation]()


def test_inspection_and_planning_remain_available(store, read_only_store):
    _, status = gated(store)
    db = Database(read_only_store, Registry(Product))
    inspection = db.inspect_schema(Product)
    assert inspection.status == "matching"
    assert inspection.migration_id == status.migration_id
    assert inspection.installed.fingerprint == status.source_fingerprint
    assert db.plan_migration(Product).is_noop
    # Clients predating the gate still reject the installed fingerprint barrier.
    assert store.get(schema_path(Product.__global__)) != status.source_fingerprint.encode()


@pytest.mark.parametrize("value", [b"not-json", b"{}", b'{"format_version":1}', b"null"])
def test_malformed_checkpoint_is_rejected(store, value):
    migration_id = "a" * 32
    store.set(job_path(migration_id), value)
    with pytest.raises(StorageError):
        load_status(store, migration_id)


@pytest.mark.parametrize("version", [True, 1.0])
def test_checkpoint_format_version_requires_an_integer(store, version):
    _, status = gated(store)
    root = job_path(status.migration_id)
    document = json.loads(store.get(root))
    document["format_version"] = version
    store.set(root, canonical_json(document))
    with pytest.raises(StorageError):
        load_status(store, status.migration_id)


def test_barrier_without_owner_is_rejected(store):
    db, _ = gated(store)
    store.delete_tree(GATE)
    with pytest.raises(StorageError):
        db.inspect_schema(Product)


def test_wrong_owner_cannot_advance(store):
    _, status = gated(store)
    with pytest.raises(MigrationError):
        require_owner(store, replace(status, migration_id="b" * 32))


def test_migration_global_is_reserved():
    class Invalid(Model):
        __global__ = "^YHMFMigration"
        id: str = Field(primary_key=True)

    from mccann_hub.yhmf.errors import SchemaError

    with pytest.raises(SchemaError):
        Registry(Invalid)


def test_unrelated_models_are_also_gated(store):
    db, _ = gated(store)

    class Order(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)

    other = Database(db.store, Registry(Order))
    with pytest.raises(MaintenanceActive):
        other.insert(Order(id="O1"))
    assert store.children(Path(Order.__global__), limit=1) == ()
