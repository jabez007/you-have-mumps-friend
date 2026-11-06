# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import (
    MaintenanceActive,
    NotFound,
    QueryError,
    SchemaError,
    StorageError,
    TransactionError,
)
from tests.test_partial_mutations import ObservedStore


class Child(Model):
    id: int = Field(primary_key=True)
    value: str = ""


class Root(Model):
    __global__ = "^YHMFTestWarehouse"
    id: int = Field(primary_key=True)
    children: Children[Child] | None = ChildField(key="id", default=None)


def test_page_of_25_does_not_read_other_children_in_a_thousand_child_collection(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Root))
    db.insert(Root(id=1, children={key: Child(id=key) for key in range(1000)}))
    observed.operations.clear()
    page = db.children(Root, 1, collection=("children",), limit=25)
    assert [child.id for child in page] == list(range(25))
    record_reads = [
        path
        for method, path in observed.operations
        if method == "get"
        and path.global_name == Root.__global__
        and len(path.subscripts) >= 3
        and path.subscripts[1] == b"children"
    ]
    assert {int(path.subscripts[2]) for path in record_reads} == set(range(25))
    assert len(observed.operations) < 120
    observed.operations.clear()
    assert db.count(Root, 1, collection=("children",)) == 1000
    assert len(observed.operations) < 20
    assert not any(
        path == Path(Root.__global__, (b"1", b"children")) and method == "children"
        for method, path in observed.operations
    )


def test_pages_use_exclusive_typed_native_keys_and_can_share_one_transaction(store):
    db = Database(store, Registry(Root))
    db.insert(Root(id=1, children={key: Child(id=key) for key in (-10, -2, 0, 2, 10)}))

    def read(tx):
        first = tx.children(Root, 1, collection=("children",), limit=2)
        second = tx.children(Root, 1, collection=("children",), limit=2, after=first[-1].id)
        return [item.id for item in first + second]

    assert db.atomic(read) == [-10, -2, 0, 2]
    assert [item.id for item in db.children(Root, 1, collection=("children",), after=2)] == [10]
    assert db.children(Root, 1, collection=("children",), after=10) == []
    with pytest.raises(SchemaError):
        db.children(Root, 1, collection=("children",), after="2")


@pytest.mark.parametrize("size", [0, -1, 1001, True, 1.5])
def test_invalid_page_limits_fail_without_storage_work(store, size):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Root))
    with pytest.raises(QueryError):
        db.children(Root, 1, collection=("children",), limit=size)
    assert observed.operations == []


def test_null_empty_and_missing_collections_are_distinct(store):
    db = Database(store, Registry(Root))
    db.insert(Root(id=1))
    for operation in (db.count, db.children):
        with pytest.raises(NotFound):
            operation(Root, 1, collection=("children",))
        with pytest.raises(NotFound):
            operation(Root, 2, collection=("children",))
    db.save(Root(id=1, children={}))
    assert db.children(Root, 1, collection=("children",)) == []
    assert db.count(Root, 1, collection=("children",)) == 0


def test_paged_child_identity_and_presence_are_validated(store):
    db = Database(store, Registry(Root))
    db.insert(Root(id=1, children={1: Child(id=1)}))
    store.set(Path(Root.__global__, (b"1", b"children", b"1", b"id")), b"i:2")
    with pytest.raises(StorageError, match="identity"):
        db.children(Root, 1, collection=("children",))


def test_expired_handles_cannot_page_or_count(store):
    db = Database(store, Registry(Root))
    tx = db.atomic(lambda tx: tx)
    for operation in (tx.children, tx.count):
        with pytest.raises(TransactionError):
            operation(Root, 1, collection=("children",))


def test_string_keys_and_deleted_cursors_keep_exclusive_native_pagination(store):
    class TextChild(Model):
        id: str = Field(primary_key=True)

    class TextRoot(Model):
        __global__ = Root.__global__
        id: int = Field(primary_key=True)
        children: Children[TextChild] = ChildField(key="id")

    db = Database(store, Registry(TextRoot))
    db.insert(TextRoot(id=1, children={key: TextChild(id=key) for key in ("", "0", "01", "雪")}))
    first = db.children(TextRoot, 1, collection=("children",), limit=2)
    assert [item.id for item in first] == ["", "0"]
    db.delete_child(TextRoot, 1, at=("children", "0"))
    assert [item.id for item in db.children(TextRoot, 1, collection=("children",), after="0")] == [
        "01",
        "雪",
    ]
    assert db.count(TextRoot, 1, collection=("children",)) == 3


def test_pages_and_count_observe_the_maintenance_gate(store):
    class Target(Root):
        __schema_version__ = 2

    old = Database(store, Registry(Root))
    old.insert(Root(id=1, children={}))
    db = Database(store, Registry(Target))
    status = db.begin_migration(Root, Target, plan=db.plan_migration(Target))
    for operation in (old.children, old.count):
        with pytest.raises(MaintenanceActive):
            operation(Root, 1, collection=("children",))
    db.abort_migration(status.migration_id)
    assert old.count(Root, 1, collection=("children",)) == 0
