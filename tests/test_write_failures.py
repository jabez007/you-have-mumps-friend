# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Registry
from mccann_hub.yhmf.errors import TransactionError


class Record(Model):
    __global__ = "^FailureRecord"
    id: int = Field(primary_key=True)
    description: str


class FailingStore(MemoryStore):
    fail_at: bytes | None = None

    def set(self, path, value):
        super().set(path, value)
        if self.fail_at is not None and path.subscripts[-1:] == (self.fail_at,):
            raise OSError("injected storage failure")


@pytest.mark.parametrize("fail_at", [b"description", b"snapshot"])
def test_caught_storage_failure_still_aborts_the_transaction(fail_at):
    store = FailingStore()
    db = Database(store, Registry(Record))
    if fail_at == b"description":
        db.insert(Record(id=1, description="before"))
    store.fail_at = fail_at

    def catch_error(tx):
        try:
            tx.save(Record(id=1, description="after"))
        except OSError:
            pass

    with pytest.raises(TransactionError, match="failed write"):
        db.atomic(catch_error)
    store.fail_at = None
    if fail_at == b"description":
        assert db.get(Record, 1).description == "before"
    else:
        assert store.get(Path("^YHMFSchema", (b"FailureRecord",))) is None
        assert store.children(Path("^FailureRecord"), limit=1) == ()
