# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from mccann_hub.yhmf import MemoryStore, NativeStore, Path


class ReadOnlyStore:
    def __init__(self, store):
        self.store = store

    def get(self, path):
        return self.store.get(path)

    def children(self, path, *, limit, after=b"", reverse=False):
        return self.store.children(path, limit=limit, after=after, reverse=reverse)

    def atomic(self, callback):
        return self.store.atomic(callback)

    def set(self, path, value):
        raise AssertionError("Read-only operation attempted to write")

    def delete_tree(self, path):
        raise AssertionError("Read-only operation attempted to delete")


@pytest.fixture
def read_only_store(store):
    return ReadOnlyStore(store)


@pytest.fixture(params=["memory", pytest.param("native", marks=pytest.mark.native)])
def store(request):
    if request.param == "native":
        if os.environ.get("YHMF_TEST_NATIVE") != "1":
            pytest.skip("Set YHMF_TEST_NATIVE=1 with a disposable YottaDB database")
        result = NativeStore()
    else:
        result = MemoryStore()
    roots = [
        Path("^YHMFTestStorage"),
        Path("^YHMFTestProduct"),
        Path("^YHMFTestWarehouse"),
        Path("^YHMFSchema"),
        Path("^YHMFRef"),
        Path("^YHMFIndex"),
        Path("^YHMFUnique"),
        Path("^YHMFMigration"),
        Path("^YHMFTestOrder"),
        Path("^InventoryProduct"),
        Path("^InventoryLot"),
        Path("^InventoryWarehouse"),
        Path("^InventoryMovement"),
        Path("^InventorySupplier"),
        Path("^InventoryLocation"),
        Path("^InventoryBin"),
        Path("^InventoryStockMove"),
    ]
    for root in roots:
        result.delete_tree(root)
    yield result
    for root in roots:
        result.delete_tree(root)
