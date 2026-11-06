# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Path
from mccann_hub.yhmf.errors import StorageError
from tests.test_transaction_control import SignalEngine, native


class NativeError(Exception):
    def __init__(self, code):
        self.number = code

    def code(self):
        return self.number


class Engine(SignalEngine):
    YDBError = NativeError
    YDB_ERR_REC2BIG = 11
    YDB_ERR_KEY2BIG = 12


@pytest.mark.parametrize("code,setting", [(11, "record_size"), (12, "key_size")])
@pytest.mark.parametrize("method", ["get", "set", "delete_tree", "children"])
def test_native_region_limit_errors_preserve_cause_and_name_the_setting(code, setting, method):
    store = native(Engine())
    error = NativeError(code)

    def fail(*args):
        raise error

    operation = "subscript_next" if method == "children" else method
    setattr(store._ydb, operation, fail)
    path = Path("^YHMFTestStorage")
    with pytest.raises(StorageError, match=setting) as caught:
        if method == "set":
            store.set(path, b"data")
        elif method == "children":
            store.children(path, limit=1)
        else:
            getattr(store, method)(path)
    assert caught.value.__cause__ is error
    assert caught.value.__cause__.code() == code


def test_unrelated_native_errors_are_not_reclassified():
    store = native(Engine())
    error = NativeError(99)

    def fail(*args):
        raise error

    store._ydb.get = fail
    with pytest.raises(NativeError) as caught:
        store.get(Path("^YHMFTestStorage"))
    assert caught.value is error
