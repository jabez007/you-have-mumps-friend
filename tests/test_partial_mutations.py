# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal
from typing import Annotated

import pytest
from pydantic import AfterValidator, ConfigDict, ValidationError, model_validator

from mccann_hub.yhmf import Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import MutationError, NotFound, ReferenceViolation, UniqueViolation
from tests.mutation_models import Lot, Product, Warehouse, warehouse


@pytest.fixture
def db(store):
    result = Database(store, Registry(Product, Warehouse))
    result.insert(warehouse(2))
    return result


def test_patch_root_embedded_and_deep_child_without_replacing_ownership(db):
    db.patch(Warehouse, "W", changes={"name": "Renamed"})
    db.patch(Warehouse, "W", at=("address",), changes={"city": "Austin"})
    db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"quantity": Decimal("25.50")})
    result = db.get(Warehouse, "W")
    assert result.name == "Renamed" and result.address.city == "Austin"
    assert result.bins["A"].lots["0"].quantity == Decimal("25.50")
    assert result.bins["A"].lots["1"].quantity == Decimal("0")
    owned = db.get(Warehouse, "W", at=("bins", "A", "lots", "0"))
    assert isinstance(owned, Lot) and owned.quantity == Decimal("25.50")
    assert db.query(Warehouse).where(**{"bins.lots.quantity": Decimal("25.5")}).all(limit=5) == [
        result
    ]


@pytest.mark.parametrize("changes", [{"id": "different"}, {"bins": {}}, {"unknown": 1}, {}])
def test_patch_rejects_identity_owned_replacement_and_unknown_fields(db, changes):
    with pytest.raises(MutationError):
        db.patch(Warehouse, "W", changes=changes)
    assert db.get(Warehouse, "W").bins["A"].lots.keys() == {"0", "1"}


@pytest.mark.parametrize("at", [("bins",), ("bins", 1), ("unknown",), ("name",)])
def test_addressing_rejects_ambiguous_or_incorrect_owned_paths(db, at):
    with pytest.raises(MutationError):
        db.patch(Warehouse, "W", at=at, changes={"name": "No"})


def test_patch_requires_existing_root_and_child(db):
    with pytest.raises(NotFound):
        db.patch(Warehouse, "absent", changes={"name": "No"})
    with pytest.raises(NotFound):
        db.patch(Warehouse, "W", at=("bins", "absent"), changes={"title": "No"})


def test_failed_validation_unique_and_reference_claims_leave_nodes_and_indexes_unchanged(db):
    path = ("bins", "A", "lots", "0")
    db.patch(Warehouse, "W", at=path, changes={"serial": "claimed"})
    with pytest.raises(UniqueViolation):
        db.patch(
            Warehouse,
            "W",
            at=("bins", "A", "lots", "1"),
            changes={"serial": "claimed", "note": "no"},
        )
    with pytest.raises(ValidationError):
        db.patch(Warehouse, "W", at=path, changes={"quantity": Decimal("-1")})
    with pytest.raises(ReferenceViolation):
        db.patch(Warehouse, "W", at=path, changes={"product": Reference[Product](key="absent")})
    assert db.get(Warehouse, "W").bins["A"].lots["1"].note == ""
    assert db.get(Warehouse, "W").bins["A"].lots["0"].product is None
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_patch_reference_changes_update_reverse_counts_and_equality_indexes(db):
    db.insert(Product(id="P", name="Target"))
    path = ("bins", "A", "lots", "0")
    db.patch(Warehouse, "W", at=path, changes={"product": Reference[Product](key="P")})
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P")
    assert (
        db.query(Warehouse).where(**{"bins.lots.product": Reference[Product](key="P")}).all(limit=5)
    )
    db.patch(Warehouse, "W", at=path, changes={"product": None})
    assert db.delete(Product, "P")
    assert db.check_indexes(Warehouse, limit=5).issues == ()


def test_leaf_cross_field_validation_is_preserved(store):
    class Range(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        low: int
        high: int

        @model_validator(mode="after")
        def ordered(self):
            if self.low > self.high:
                raise ValueError("unordered")
            return self

    db = Database(store, Registry(Range))
    db.insert(Range(id="R", low=1, high=3))
    with pytest.raises(ValidationError):
        db.patch(Range, "R", changes={"low": 4})
    db.patch(Range, "R", changes={"low": 4, "high": 5})
    assert db.get(Range, "R").low == 4


@pytest.mark.parametrize("kind", ["model", "annotated"])
def test_ancestor_custom_validation_is_explicitly_rejected(store, kind):
    if kind == "model":

        class Validated(Warehouse):
            @model_validator(mode="after")
            def owned_constraint(self):
                if any(
                    lot.quantity > 10 for bin in self.bins.values() for lot in bin.lots.values()
                ):
                    raise ValueError("Too much")
                return self
    else:

        class Validated(Warehouse):
            name: Annotated[str, AfterValidator(lambda value: value)] = "Main"

    db = Database(store, Registry(Product, Validated))
    db.insert(Validated.model_validate(warehouse().model_dump()))
    with pytest.raises(MutationError, match="custom validators"):
        db.patch(Validated, "W", at=("bins", "A", "lots", "0"), changes={"quantity": Decimal("11")})


def test_shallow_field_validation_honors_owner_model_config(store):
    class Limited(Warehouse):
        model_config = ConfigDict(str_max_length=4)

    db = Database(store, Registry(Product, Limited))
    db.insert(Limited(id="W"))
    with pytest.raises(ValidationError):
        db.patch(Limited, "W", changes={"name": "Too long"})


def test_caught_patch_failure_poisoned_transaction_rolls_back_prior_mutations(db):
    from mccann_hub.yhmf.errors import TransactionError

    def callback(tx):
        tx.patch(Warehouse, "W", changes={"name": "No"})
        try:
            tx.patch(
                Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"quantity": Decimal("-1")}
            )
        except ValidationError:
            pass

    with pytest.raises(TransactionError):
        db.atomic(callback)
    assert db.get(Warehouse, "W").name == "Main"


class ObservedStore:
    def __init__(self, store):
        self.store = store
        self.operations = []

    def get(self, path):
        self.operations.append(("get", path))
        return self.store.get(path)

    def children(self, path, *, limit, after=b""):
        self.operations.append(("children", path))
        return self.store.children(path, limit=limit, after=after)

    def set(self, path, value):
        self.operations.append(("set", path))
        self.store.set(path, value)

    def delete_tree(self, path):
        self.operations.append(("delete", path))
        self.store.delete_tree(path)

    def atomic(self, callback):
        return self.store.atomic(callback)


def test_scalar_work_is_independent_of_unrelated_children_and_touches_only_affected_indexes(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    counts = []
    for size in (1, 100):
        db.save(warehouse(size))
        observed.operations.clear()
        db.patch(Warehouse, "W", at=("bins", "A", "lots", "0"), changes={"quantity": Decimal("3")})
        counts.append(len(observed.operations))
        for operation, path in observed.operations:
            assert b"s:1" not in path.subscripts
            if operation in {"delete", "set"}:
                assert path.global_name == "^YHMFIndex" or path == Path(
                    "^YHMFTestWarehouse", (b"s:W", b"bins", b"s:A", b"lots", b"s:0", b"quantity")
                )
                if path.global_name == "^YHMFIndex":
                    assert path.subscripts[1] == b"bins.lots.quantity"
    assert counts[0] == counts[1]
