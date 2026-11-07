# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Path, Registry
from mccann_hub.yhmf.errors import SchemaMismatch, StorageError, TransactionError
from mccann_hub.yhmf.schemas import schema_path
from tests.adversarial_support import dump
from tests.mutation_models import Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore


def test_raw_path_is_scoped_to_a_managed_owner_without_loading_the_aggregate(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(warehouse(1000))
    observed.operations.clear()

    def inspect(tx):
        path = tx.raw_path(Warehouse, "W", at=("address",))
        assert path == Path(Warehouse.__global__, (b"s:W", b"address"))
        return tx.store.get(path.child(b"city"))

    assert db.atomic(inspect) == b"s:Chicago"
    assert len(observed.operations) < 20
    assert not any(b"bins" in path.subscripts for _, path in observed.operations)


def test_raw_writes_are_transactional_and_explicitly_bypass_index_maintenance(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse(1))

    def abort(tx):
        root = tx.raw_path(Warehouse, "W")
        tx.store.set(root.child(b"name"), b"s:Changed")
        raise RuntimeError("abort")

    before = dump(store, (Warehouse.__global__, "^YHMFIndex"))
    with pytest.raises(RuntimeError):
        db.atomic(abort)
    assert dump(store, (Warehouse.__global__, "^YHMFIndex")) == before
    db.atomic(lambda tx: tx.store.set(tx.raw_path(Warehouse, "W").child(b"name"), b"s:Changed"))
    assert db.get(Warehouse, "W").name == "Changed"
    with pytest.raises(StorageError):
        db.query(Warehouse).where(name="Main").all()
    assert db.query(Warehouse).where(name="Changed").all() == []
    assert db.check_indexes(Warehouse).issues


def test_raw_path_requires_an_active_atomic_handle(store):
    db = Database(store, Registry(Product, Warehouse))
    with pytest.raises(TransactionError, match="atomic"):
        db.raw_path(Warehouse, "W")
    expired = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError, match="expired"):
        expired.raw_path(Warehouse, "W")


def test_raw_path_never_installs_metadata_over_unmanaged_data(store):
    db = Database(store, Registry(Product, Warehouse))
    root = db.registry.spec(Warehouse).path("raw")
    store.set(root.child(b"unknown"), b"legacy")
    before = dump(store, (Warehouse.__global__, "^YHMFSchema"))
    with pytest.raises((SchemaMismatch, StorageError)):
        db.atomic(lambda tx: tx.raw_path(Warehouse, "raw"))
    assert dump(store, (Warehouse.__global__, "^YHMFSchema")) == before
    assert store.get(schema_path(Warehouse.__global__)) is None
