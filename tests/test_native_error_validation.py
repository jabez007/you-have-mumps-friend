# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure paths must reach a stable native read attempt before escaping."""

import pytest

from mccann_hub.yhmf import NativeStore
from mccann_hub.yhmf.errors import StorageError


class Restart(Exception):
    pass


class Rollback(Exception):
    pass


class EngineError(Exception):
    pass


class Engine:
    YDBTPRestart = Restart
    YDBTPRollback = Rollback
    YDBError = EngineError
    YDB_OK = 0

    def __init__(self):
        self.restarts = 0

    def get(self, name):
        assert name == "$trestart"
        return str(self.restarts).encode()

    def tp(self, callback):
        for _ in range(8):
            try:
                return callback()
            except Restart:
                self.restarts += 1
        raise AssertionError("Native failure revalidation did not terminate")


def native():
    store = NativeStore.__new__(NativeStore)
    store._ydb = Engine()
    return store


def test_transient_integrity_error_is_revalidated_before_it_escapes():
    store = native()
    attempts = []

    def operation():
        attempts.append(store._ydb.restarts)
        if len(attempts) == 1:
            raise StorageError("Mixed checkpoint and secondary entries")
        return "fresh result"

    assert store.atomic(operation) == "fresh result"
    assert attempts == [0, 1]


@pytest.mark.parametrize("error", [StorageError("persistent damage"), ValueError("invalid values")])
def test_persistent_validation_error_escapes_at_the_native_stable_attempt(error):
    store = native()
    attempts = []

    def operation():
        attempts.append(store._ydb.restarts)
        raise error

    with pytest.raises(type(error), match=str(error)):
        store.atomic(operation)
    assert attempts == [0, 1, 2, 3]


@pytest.mark.parametrize("error", [RuntimeError("abort"), Rollback(), EngineError()])
def test_intentional_aborts_and_native_signals_are_not_retried(error):
    store = native()
    with pytest.raises(type(error)):
        store.atomic(lambda: (_ for _ in ()).throw(error))
    assert store._ydb.restarts == 0
