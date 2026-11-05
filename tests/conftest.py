# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from mccann_hub.yhmf import MemoryStore, NativeStore, Path


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
    ]
    for root in roots:
        result.delete_tree(root)
    yield result
    for root in roots:
        result.delete_tree(root)
