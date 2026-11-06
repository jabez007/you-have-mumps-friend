# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import NotFound, QueryError, SchemaError, TransactionError
from tests.test_partial_mutations import ObservedStore
from tests.test_transaction_control import Restart, SignalEngine, native


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    name: str


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)


def seed(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(Product(id=1, name="one"))
    db.insert(Product(id=2, name="two"))
    db.insert(Warehouse(id="1"))
    observed.operations.clear()
    return db, observed


def test_resolve_and_batch_reads_are_explicit_ordered_and_deduplicated(store):
    db, observed = seed(store)
    refs = [Reference[Product](key=2), Reference[Product](key=1), Reference[Product](key=2)]
    result = db.resolve_many(refs)
    assert [record.name for record in result] == ["two", "one", "two"]
    assert result[0] is result[2]
    leaf = Path(Product.__global__, (b"2", b"name"))
    assert observed.operations.count(("get", leaf)) == 1
    assert db.atomic(lambda tx: tx.resolve(refs[0])).name == "two"
    observed.operations.clear()
    assert [record.id for record in db.get_many(Product, [2, 1, 2])] == [2, 1, 2]
    assert observed.operations.count(("get", leaf)) == 1


def test_resolution_distinguishes_declared_targets_and_typed_keys(store):
    db, _ = seed(store)
    refs = [Reference[Product](key=1), Reference[Warehouse](key="1")]
    result = db.resolve_many(refs)
    assert type(result[0]) is Product and type(result[1]) is Warehouse
    with pytest.raises(SchemaError):
        db.resolve(Reference[Product](key="1"))
    with pytest.raises(SchemaError):
        db.get_many(Product, [1, True])  # bool must not hit the cached integer key.


@pytest.mark.parametrize("operation", ["resolve", "resolve_many", "get_many"])
def test_missing_batch_target_is_value_free_and_does_not_write(store, operation):
    db, observed = seed(store)
    with pytest.raises(NotFound) as error:
        if operation == "resolve":
            db.resolve(Reference[Product](key=123456789))
        elif operation == "resolve_many":
            db.resolve_many([Reference[Product](key=1), Reference[Product](key=123456789)])
        else:
            db.get_many(Product, [1, 123456789])
    assert "123456789" not in str(error.value)
    if operation.startswith("resolve"):
        assert "Reference target" in str(error.value)
    assert not any(method in {"set", "delete"} for method, _ in observed.operations)


@pytest.mark.parametrize("operation", ["resolve_many", "get_many"])
def test_batches_reject_unbounded_or_oversized_inputs_before_storage(store, operation):
    db, observed = seed(store)
    for values in ([1] * 1001, iter([1]), "not a batch"):
        with pytest.raises(QueryError):
            if operation == "get_many":
                db.get_many(Product, values)
            else:
                db.resolve_many(values)
    assert observed.operations == []


def test_empty_batches_and_expired_handles(store):
    db, _ = seed(store)
    assert db.get_many(Product, []) == [] and db.resolve_many([]) == []
    tx = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError):
        tx.get_many(Product, [])
    with pytest.raises(TransactionError):
        tx.resolve_many([])


def test_unparameterized_reference_is_not_a_traversable_target(store):
    db, _ = seed(store)
    with pytest.raises(SchemaError, match="concrete target"):
        db.resolve(Reference(key=1))


def test_a_restart_discards_cached_targets_before_the_next_attempt():
    first = Path(Product.__global__, (b"1", b"name"))
    second = Path(Product.__global__, (b"2", b"name"))

    class ChangingEngine(SignalEngine):
        armed = False

        def _fault(self, method, path):
            super()._fault(method, path)
            if self.armed and method == "get" and path == second:
                self.armed = False
                raise Restart()

        def tp(self, callback):
            for _ in range(8):
                try:
                    return self.memory.atomic(callback)
                except Restart:
                    self.restarts += 1
                    # Simulate another committed writer between native attempts.
                    self.memory.set(first, b"s:changed")
            raise AssertionError("Unbounded transaction restart")

    engine = ChangingEngine()
    db, _ = seed(native(engine))
    engine.armed = True
    values = db.resolve_many([Reference[Product](key=1), Reference[Product](key=2)])
    assert [value.name for value in values] == ["changed", "two"]
    assert engine.restarts == 1
