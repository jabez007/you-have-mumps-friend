# SPDX-License-Identifier: GPL-3.0-or-later
import sys

import pytest

from mccann_hub.yhmf import Database, Field, Model, Registry
from mccann_hub.yhmf.errors import StorageError
from tests.adversarial_support import dump


def test_unrepresentable_integer_rolls_back_prior_atomic_writes(store):
    class Row(Model):
        __global__ = "^YHMFTestProduct"
        id: int = Field(primary_key=True)
        amount: int

    db = Database(store, Registry(Row))
    db.insert(Row(id=1, amount=2))
    before = dump(store, (Row.__global__, "^YHMFSchema"))

    def operation(tx):
        tx.patch(Row, 1, changes={"amount": 3})
        tx.insert(Row(id=2, amount=10**5000))

    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(StorageError):
            db.atomic(operation)
    finally:
        sys.set_int_max_str_digits(previous)
    assert dump(store, (Row.__global__, "^YHMFSchema")) == before
