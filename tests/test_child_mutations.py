# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest
from pydantic import ValidationError, model_validator

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import (
    AlreadyExists,
    MutationError,
    NotFound,
    StorageError,
    UniqueViolation,
)
from tests.mutation_models import Bin, Lot, Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore


@pytest.fixture
def db(store):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse(2))
    return db


def test_child_insert_patch_delete_and_atomic_composition(db):
    def receive(tx):
        tx.insert_child(
            Warehouse,
            "W",
            collection=("bins", "A", "lots"),
            record=Lot(id="new", quantity=Decimal("2.5")),
        )
        tx.patch(Warehouse, "W", at=("bins", "A", "lots", "new"), changes={"note": "received"})
        tx.delete_child(Warehouse, "W", at=("bins", "A", "lots", "0"))

    db.atomic(receive)
    lots = db.get(Warehouse, "W").bins["A"].lots
    assert lots.keys() == {"new", "1"}
    assert lots["new"].quantity == Decimal("2.5") and lots["new"].note == "received"
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_insert_child_subtree_uses_complete_validation_and_scoped_identity(db):
    record = Bin(id="B", lots={"0": Lot(id="0", serial="second", quantity=Decimal("3"))})
    db.insert_child(Warehouse, "W", collection=("bins",), record=record)
    assert db.get(Warehouse, "W").bins["B"] == record
    invalid = Bin(id="C", lots={"wrong": Lot(id="right")})
    with pytest.raises(StorageError, match="identity"):
        db.insert_child(Warehouse, "W", collection=("bins",), record=invalid)
    bad = Lot(id="bad").model_copy(update={"quantity": Decimal("-1")})
    with pytest.raises(ValidationError):
        db.insert_child(Warehouse, "W", collection=("bins", "A", "lots"), record=bad)


def test_child_duplicate_unique_failure_and_missing_delete_leave_counts_unchanged(db):
    db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"serial": "claimed"})
    with pytest.raises(AlreadyExists):
        db.insert_child(Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id="0"))
    with pytest.raises(UniqueViolation):
        db.insert_child(
            Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id="new", serial="claimed")
        )
    with pytest.raises(NotFound):
        db.delete_child(Warehouse, "W", at=("bins", "A", "lots", "absent"))
    assert db.get(Warehouse, "W").bins["A"].lots.keys() == {"0", "1"}
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_child_deletion_releases_nested_references_and_unique_claims(db):
    db.insert(Product(id="P", name="Target"))
    db.insert_child(
        Warehouse,
        "W",
        collection=("bins",),
        record=Bin(
            id="B", lots={"L": Lot(id="L", serial="unique", product=Reference[Product](key="P"))}
        ),
    )
    db.delete_child(Warehouse, "W", at=("bins", "B"))
    assert db.delete(Product, "P")
    db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"serial": "unique"})
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_child_mutations_honor_collection_length_constraints(store):
    class Limited(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        lots: Children[Lot] = ChildField(key="id", min_length=1, max_length=2)

    db = Database(store, Registry(Product, Limited))
    db.insert(Limited(id="W", lots={"a": Lot(id="a")}))
    with pytest.raises(MutationError, match="length"):
        db.delete_child(Limited, "W", at=("lots", "a"))
    db.insert_child(Limited, "W", collection=("lots",), record=Lot(id="b"))
    with pytest.raises(MutationError, match="length"):
        db.insert_child(Limited, "W", collection=("lots",), record=Lot(id="c"))
    assert len(db.get(Limited, "W").lots) == 2


def test_child_insert_preserves_record_node_and_collection_budgets(db):
    for size in (1000,):
        db.save(warehouse(size))
        with pytest.raises(StorageError, match="1000"):
            db.insert_child(
                Warehouse, "W", collection=("bins", "A", "lots"), record=Lot(id="extra")
            )
    large = warehouse(1000)
    large.bins["B"] = Bin(id="B", lots={str(i): Lot(id=str(i)) for i in range(663)})
    db.save(large)  # Near the legacy 10,000 value-node limit.
    with pytest.raises(StorageError, match="node budget"):
        db.insert_child(Warehouse, "W", collection=("bins", "B", "lots"), record=Lot(id="extra"))
    assert len(db.get(Warehouse, "W").bins["B"].lots) == 663


def test_deleting_last_child_keeps_empty_collection_marker_and_other_subtrees(db):
    db.delete_child(Warehouse, "W", at=("bins", "A", "lots", "0"))
    db.delete_child(Warehouse, "W", at=("bins", "A", "lots", "1"))
    path = Path("^YHMFTestWarehouse", (b"s:W", b"bins", b"s:A", b"lots"))
    assert db.store.get(path) == b"c:0" and db.store.children(path, limit=1) == ()
    assert db.get(Warehouse, "W").address.city == "Chicago"


def test_child_operations_roll_back_together(db):
    original = db.get(Warehouse, "W")

    def abort(tx):
        tx.insert_child(Warehouse, "W", collection=("bins",), record=Bin(id="B"))
        tx.delete_child(Warehouse, "W", at=("bins", "A"))
        raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        db.atomic(abort)
    assert db.get(Warehouse, "W") == original
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_collection_ancestor_validation_is_rejected_and_new_child_validator_runs(store):
    class ValidatedBin(Bin):
        @model_validator(mode="after")
        def title_rule(self):
            if not self.title:
                raise ValueError("title required")
            return self

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        bins: Children[ValidatedBin] = ChildField(key="id")

    db = Database(store, Registry(Product, Root))
    db.insert(Root(id="W"))
    with pytest.raises(ValidationError):
        db.insert_child(
            Root, "W", collection=("bins",), record=ValidatedBin.model_construct(id="B")
        )
    db.insert_child(Root, "W", collection=("bins",), record=ValidatedBin(id="B", title="Valid"))
    with pytest.raises(MutationError, match="custom validators"):
        db.insert_child(Root, "W", collection=("bins", "B", "lots"), record=Lot(id="L"))


def test_child_delete_does_not_read_sibling_subtrees(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(warehouse(100))
    observed.operations.clear()
    db.delete_child(Warehouse, "W", at=("bins", "A", "lots", "0"))
    assert not any(b"s:1" in path.subscripts for _, path in observed.operations)
    deletes = [
        path
        for operation, path in observed.operations
        if operation == "delete" and path.global_name == Warehouse.__global__
    ]
    assert deletes == [Path("^YHMFTestWarehouse", (b"s:W", b"bins", b"s:A", b"lots", b"s:0"))]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("operation", ["insert_child", "save"])
def test_child_insert_revalidates_copies_even_when_instance_revalidation_is_disabled(
    store, nested, operation
):
    from pydantic import ConfigDict

    class ReusedLot(Lot):
        model_config = ConfigDict(revalidate_instances="never")

    class Group(Model):
        id: str = Field(primary_key=True)
        lots: Children[ReusedLot] = ChildField(key="id")

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        lots: Children[ReusedLot] = ChildField(key="id")
        groups: Children[Group] = ChildField(key="id")

    db = Database(store, Registry(Product, Root))
    db.insert(Root(id="W"))
    invalid = ReusedLot(id="bad").model_copy(update={"quantity": Decimal("-1")})
    collection = ("groups",) if nested else ("lots",)
    record = Group(id="G", lots={"bad": invalid}) if nested else invalid
    with pytest.raises(ValidationError):
        if operation == "insert_child":
            db.insert_child(Root, "W", collection=collection, record=record)
        else:
            root = Root(id="W").model_copy(update={collection[0]: {record.id: record}})
            db.save(root)
    assert db.get(Root, "W").lots == db.get(Root, "W").groups == {}
