# SPDX-License-Identifier: GPL-3.0-or-later
"""Known external boundaries can be blocked; arbitrary callbacks are not read-only."""

from unittest.mock import Mock

import pytest

from mccann_hub.yhmf import Database, NativeStore, Path, Registry
from tests.adversarial_support import dump
from tests.mutation_models import Product


def test_instrumented_external_boundary_rejects_and_rolls_back_the_callback(store):
    db = Database(store, Registry(Product))
    db.insert(Product(id="P", name="Before"))
    before = dump(store, (Product.__global__, "^YHMFIndex", "^YHMFUnique"))
    publish = Mock(side_effect=AssertionError("External side effect inside transaction"))

    def unsafe(tx):
        tx.patch(Product, "P", changes={"name": "After"})
        publish("message")

    with pytest.raises(AssertionError, match="External side effect"):
        db.atomic(unsafe)
    publish.assert_called_once_with("message")
    assert dump(store, (Product.__global__, "^YHMFIndex", "^YHMFUnique")) == before


@pytest.mark.native
@pytest.mark.adversarial
def test_committing_an_error_sentinel_is_unsafe_even_without_observed_adapter_writes(store):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires real native transactions and raw writes")
    yottadb = pytest.importorskip("yottadb")
    path = Path("^YHMFTestStorage", (b"effect",))
    attempts = []
    store.set(path, b"before")

    def reject_after_raw_write():
        attempts.append(int(yottadb.get("$trestart")))
        yottadb.set(path.global_name, path.subscripts, b"after")
        raise ValueError("rejected")

    def unsafe_attempt():
        try:
            reject_after_raw_write()
        except ValueError:
            return yottadb.YDB_OK  # Proposed read-validation optimization: commits raw writes.

    yottadb.tp(unsafe_attempt)
    assert store.get(path) == b"after"
    store.set(path, b"before")
    attempts.clear()
    with pytest.raises(ValueError, match="rejected"):
        store.atomic(reject_after_raw_write)
    assert attempts == [0, 1, 2, 3]
    assert store.get(path) == b"before"
