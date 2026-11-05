# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Registry, SchemaSnapshot
from mccann_hub.yhmf.errors import SchemaError, SchemaMismatch, StorageError, TransactionError


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str


def test_inspection_does_not_install_an_absent_schema(store, read_only_store):
    db = Database(read_only_store, Registry(Product))
    inspection = db.inspect_schema(Product)
    assert inspection.status == "uninstalled"
    assert inspection.installed is None and inspection.has_data is False
    assert inspection.declared.global_name == Product.__global__
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None


def test_inspection_exposes_the_exact_installed_snapshot(store, read_only_store):
    db = Database(store, Registry(Product))
    db.insert(Product(id="P1", name="Main"))
    inspected = Database(read_only_store, db.registry).inspect_schema(Product)
    spec = db.registry.spec(Product)
    assert inspected.status == "matching" and inspected.has_data
    assert inspected.installed.canonical == spec.snapshot
    assert inspected.installed.fingerprint == hashlib.sha256(spec.snapshot).hexdigest()


def test_changed_declarations_can_inspect_without_advancing_installed_schema(
    store, read_only_store
):
    old_db = Database(store, Registry(Product))
    old_db.insert(Product(id="P1", name="Main"))

    class Changed(Product):
        __schema_version__ = 2
        note: str | None = None

    new_db = Database(read_only_store, Registry(Changed))
    inspection = new_db.inspect_schema(Changed)
    assert inspection.status == "different"
    assert inspection.installed.schema_version == 1
    assert inspection.declared.schema_version == 2
    assert old_db.get(Product, "P1").name == "Main"
    with pytest.raises(SchemaMismatch):
        new_db.get(Changed, "P1")


def test_unmanaged_data_is_distinguished_from_an_empty_global(store, read_only_store):
    store.set(Path("^YHMFTestProduct", (b"raw",)), b"unmanaged")
    db = Database(read_only_store, Registry(Product))
    assert db.inspect_schema(Product).status == "unmanaged"


@pytest.mark.parametrize(
    "damage",
    [
        "missing_snapshot",
        "missing_fingerprint",
        "bad_fingerprint",
        "wrong_global",
    ],
)
def test_incomplete_or_inconsistent_installed_metadata_is_rejected(store, read_only_store, damage):
    db = Database(store, Registry(Product))
    db.insert(Product(id="P1", name="Main"))
    root = Path("^YHMFSchema", (b"YHMFTestProduct",))
    if damage == "missing_snapshot":
        store.delete_tree(root.child(b"snapshot"))
    elif damage == "missing_fingerprint":
        snapshot = store.get(root.child(b"snapshot"))
        store.delete_tree(root)
        store.set(root.child(b"snapshot"), snapshot)
    elif damage == "bad_fingerprint":
        store.set(root, b"0" * 64)
    else:
        document = json.loads(store.get(root.child(b"snapshot")))
        document["global"] = "^OtherProduct"
        copied = json.dumps(
            document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        store.set(root.child(b"snapshot"), copied)
        store.set(root, hashlib.sha256(copied).hexdigest().encode("ascii"))
    with pytest.raises(StorageError):
        Database(read_only_store, db.registry).inspect_schema(Product)


def test_snapshot_values_are_explicit_and_defensively_copied():
    class Defaults(Product):
        note: str = "private-default-marker"

    snapshot = SchemaSnapshot(Registry(Defaults).spec(Defaults).snapshot)
    assert "private-default-marker" not in repr(snapshot)
    document = snapshot.to_dict()
    document["version"] = 99
    assert snapshot.to_dict()["version"] == 1
    with pytest.raises(FrozenInstanceError):
        snapshot.schema_version = 99


@pytest.mark.parametrize("data", [b"{}", b"[]", b'{"x":NaN}', b"\xff", b" " * 32767])
def test_malformed_snapshot_bytes_are_rejected(data):
    with pytest.raises(StorageError):
        SchemaSnapshot(data)


def test_snapshot_requires_canonical_json():
    snapshot = Registry(Product).spec(Product).snapshot
    spaced = json.dumps(json.loads(snapshot)).encode("ascii")
    with pytest.raises(StorageError, match="canonical"):
        SchemaSnapshot(spaced)


def test_inspection_does_not_run_default_factories(read_only_store):
    calls = []

    def default():
        calls.append("called")
        raise RuntimeError("Planning must not execute this factory")

    class Defaults(Product):
        note: str = Field(default_factory=default)

    db = Database(read_only_store, Registry(Defaults))
    assert db.inspect_schema(Defaults).status == "uninstalled"
    assert calls == []


def test_inspection_observes_transaction_handle_lifetime(read_only_store):
    db = Database(read_only_store, Registry(Product))
    tx = db.atomic(lambda handle: handle)
    with pytest.raises(TransactionError):
        tx.inspect_schema(Product)


@pytest.mark.parametrize("default", [float("nan"), float("inf"), -float("inf")])
def test_registry_cannot_generate_nonfinite_json_snapshots(default):
    class Nonfinite(Product):
        amount: float = default

    with pytest.raises(SchemaError, match="canonical JSON"):
        Registry(Nonfinite)
