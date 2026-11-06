# SPDX-License-Identifier: GPL-3.0-or-later
"""The adapter must preserve native control flow across user exception handlers."""

import pytest

from mccann_hub.yhmf import Database, MemoryStore, NativeStore, Path, Registry
from mccann_hub.yhmf.errors import TransactionError
from tests.test_native_error_validation import Engine, Restart, Rollback
from tests.test_repository import Product

TARGET = Path("^YHMFTestStorage", (b"target",))
STAGED = Path("^YHMFTestStorage", (b"staged",))


class NodeEnd(Exception):
    pass


class SignalEngine(Engine):
    YDBNodeEnd = NodeEnd

    def __init__(self, method=None, signal=None):
        super().__init__()
        self.memory = MemoryStore()
        self.method, self.signal, self.fired = method, signal, False
        self.calls = []
        self.memory.set(TARGET, b"before")
        self.memory.set(TARGET.child(b"child"), b"value")

    def _fault(self, method, path):
        self.calls.append((method, path))
        if not self.fired and method == self.method and path == TARGET:
            self.fired = True
            raise self.signal

    def get(self, name, subscripts=()):
        if name == "$trestart":
            return super().get(name)
        path = Path(name, subscripts)
        self._fault("get", path)
        return self.memory.get(path)

    def set(self, name, subscripts, value):
        path = Path(name, subscripts)
        self._fault("set", path)
        self.memory.set(path, value)

    def delete_tree(self, name, subscripts):
        path = Path(name, subscripts)
        self._fault("delete_tree", path)
        self.memory.delete_tree(path)

    def subscript_next(self, name, subscripts):
        path = Path(name, subscripts[:-1])
        self._fault("children", path)
        keys = self.memory.children(path, limit=1, after=subscripts[-1])
        if not keys:
            raise NodeEnd()
        return keys[0]

    def tp(self, callback):
        for _ in range(8):
            try:
                return self.memory.atomic(callback)
            except Restart:
                self.restarts += 1
        raise AssertionError("Restart attempts did not terminate")


def native(engine):
    store = NativeStore.__new__(NativeStore)
    store._ydb = engine
    return store


def invoke(store, method):
    if method == "get":
        return store.get(TARGET)
    if method == "set":
        return store.set(TARGET, b"after")
    if method == "delete_tree":
        return store.delete_tree(TARGET)
    return store.children(TARGET, limit=5)


@pytest.mark.parametrize("method", ["get", "set", "delete_tree", "children"])
@pytest.mark.parametrize("signal", [Restart, Rollback])
def test_swallowed_native_control_signal_cannot_commit_the_attempt(method, signal):
    error = signal("injected native signal")
    engine = SignalEngine(method, error)
    store, attempts = native(engine), []

    def callback():
        attempts.append(len(attempts) + 1)
        store.set(STAGED, str(attempts[-1]).encode())
        try:
            invoke(store, method)
        except Exception:
            pass  # A caller must not turn a native control signal into success.
        return attempts[-1]

    if signal is Restart:
        assert store.atomic(callback) == 2
        assert attempts == [1, 2]
        assert store.get(STAGED) == b"2"
    else:
        with pytest.raises(Rollback) as caught:
            store.atomic(callback)
        assert caught.value is error
        assert store.get(STAGED) is None
        assert store.get(TARGET) == b"before"
    assert store.atomic(lambda: "fresh transaction") == "fresh transaction"


def test_pending_signal_blocks_later_calls_through_another_native_store():
    error = Restart("restart")
    engine = SignalEngine("get", error)
    first, second = native(engine), native(engine)
    observed = []

    def callback():
        try:
            first.get(TARGET)
        except Exception as exc:
            observed.append(exc)
            try:
                second.set(STAGED, b"must never reach the engine")
            except Exception as later:
                observed.append(later)
        return "fresh result"

    assert first.atomic(callback) == "fresh result"
    assert engine.restarts == 1
    assert observed == [error, error]
    assert ("set", STAGED) not in engine.calls
    assert second.get(STAGED) is None


@pytest.mark.parametrize("same_store", [True, False])
def test_native_atomic_rejects_nested_entry_across_store_instances(same_store):
    first = native(SignalEngine())
    second = first if same_store else native(SignalEngine())
    callbacks = []
    with pytest.raises(TransactionError, match="Nested"):
        first.atomic(lambda: second.atomic(lambda: callbacks.append("nested")))
    assert callbacks == []
    assert second.atomic(lambda: 42) == 42


@pytest.mark.parametrize("alias", [False, True])
def test_database_rejects_outer_or_alias_handle_nesting_and_rolls_back(store, alias):
    registry = Registry(Product)
    db = Database(store, registry)
    nested = Database(store, registry) if alias else db
    callbacks = []

    def inner(tx):
        callbacks.append(True)
        tx.insert(Product(id="inner", description="must not run"))

    def outer(tx):
        tx.insert(Product(id="outer", description="must roll back"))
        nested.atomic(inner)

    with pytest.raises(TransactionError, match="Nested"):
        db.atomic(outer)
    assert callbacks == []
    assert db.scan(Product) == []
    db.insert(Product(id="later", description="fresh transaction"))
    assert db.get(Product, "later").description == "fresh transaction"


def test_caught_nesting_rejection_does_not_execute_an_inner_callback(store):
    db, callbacks = Database(store, Registry(Product)), []

    def outer(tx):
        tx.insert(Product(id="outer", description="valid outer write"))
        with pytest.raises(TransactionError):
            db.atomic(lambda inner: callbacks.append(True))
        tx.insert(Product(id="after", description="outer can continue"))

    db.atomic(outer)
    assert callbacks == []
    assert {record.id for record in db.scan(Product)} == {"outer", "after"}


def test_independent_memory_databases_keep_independent_transactions():
    first, second = MemoryStore(), MemoryStore()
    assert first.atomic(lambda: second.atomic(lambda: 42)) == 42


@pytest.mark.parametrize("signal", [Restart, Rollback])
def test_native_control_signal_takes_precedence_over_later_callback_errors(signal):
    error = signal("original control signal")
    engine = SignalEngine("get", error)
    store = native(engine)

    def callback():
        try:
            return store.get(TARGET)
        except Exception:
            raise RuntimeError("a later callback error") from None

    if signal is Restart:
        assert store.atomic(callback) == b"before"
        assert engine.restarts == 1
    else:
        with pytest.raises(Rollback) as caught:
            store.atomic(callback)
        assert caught.value is error


def test_concurrent_memory_transactions_serialize_instead_of_being_rejected_as_nested():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store = MemoryStore()
    entered, second_started, release = Event(), Event(), Event()

    def first():
        def transaction():
            entered.set()
            assert release.wait(5)
            store.set(STAGED, b"first")
            return 1

        return store.atomic(transaction)

    def second():
        second_started.set()
        return store.atomic(lambda: store.get(STAGED))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(first)
        try:
            assert entered.wait(5)
            second_result = pool.submit(second)
            assert second_started.wait(5)
        finally:
            release.set()
        assert first_result.result(timeout=5) == 1
        assert second_result.result(timeout=5) == b"first"
