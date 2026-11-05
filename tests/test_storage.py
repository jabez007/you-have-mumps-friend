# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from mccann_hub.yhmf import MemoryStore, NativeStore, Path
from mccann_hub.yhmf.errors import StorageError


@pytest.fixture(params=["memory", pytest.param("native", marks=pytest.mark.native)])
def store(request):
    if request.param == "native":
        if os.environ.get("YHMF_TEST_NATIVE") != "1":
            pytest.skip("Set YHMF_TEST_NATIVE=1 with a disposable YottaDB database")
        result = NativeStore()
    else:
        result = MemoryStore()
    root = Path("^YHMFTestStorage")
    result.delete_tree(root)
    yield result
    result.delete_tree(root)


def test_read_write_traverse_and_delete(store):
    root = Path("^YHMFTestStorage")
    assert store.get(root) is None
    for key in (b"10", b"2", b"-1", b"s:2"):
        store.set(root.child(key, b"name"), b"s:hello")
    assert store.children(root, limit=3) == (b"-1", b"2", b"10")
    assert store.children(root, limit=2, after=b"2") == (b"10", b"s:2")
    assert store.get(root.child(b"2", b"name")) == b"s:hello"
    store.delete_tree(root.child(b"2"))
    assert store.children(root, limit=10) == (b"-1", b"10", b"s:2")


def test_atomic_return_and_rollback(store):
    root = Path("^YHMFTestStorage")
    store.set(root, b"before")

    def fail():
        store.set(root, b"after")
        store.set(root.child(b"new"), b"value")
        raise ValueError("abort")

    with pytest.raises(ValueError, match="abort"):
        store.atomic(fail)
    assert store.get(root) == b"before"
    assert store.get(root.child(b"new")) is None
    assert store.atomic(lambda: 42) == 42


@pytest.mark.parametrize("name", ["Warehouse", "^", "^a(b)", "^a_b", "^a" + "b" * 31])
def test_global_validation(name):
    with pytest.raises(StorageError):
        Path(name)


@pytest.mark.parametrize("subscripts", [(b"",), (b"\0",), (b"x",) * 32, (b"x" * 600,)])
def test_path_limits(subscripts):
    with pytest.raises(StorageError):
        Path("^Test", subscripts)


def test_invalid_value_and_traversal_limits(store):
    with pytest.raises(StorageError):
        store.set(Path("^YHMFTestStorage"), b"x" * 32767)
    with pytest.raises(StorageError):
        store.children(Path("^YHMFTestStorage"), limit=0)
