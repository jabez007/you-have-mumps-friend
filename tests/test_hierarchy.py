# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Registry
from mccann_hub.yhmf.errors import NotFound, SchemaError, StorageError


class Address(Model):
    city: str
    note: str | None = None


class Lot(Model):
    number: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)


class Bin(Model):
    code: str = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="number")


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: int = Field(primary_key=True)
    name: str
    address: Address | None = None
    bins: Children[Bin] = ChildField(key="code")


@pytest.fixture
def db(store):
    return Database(store, Registry(Warehouse))


def warehouse():
    return Warehouse(
        id=7,
        name="Main",
        address=Address(city="Chicago"),
        bins={
            "A01": Bin(
                code="A01",
                lots={
                    "L-001": Lot(number="L-001", quantity=Decimal("12.50")),
                },
            )
        },
    )


def test_hierarchy_roundtrip_and_native_paths(db):
    value = warehouse()
    db.insert(value)
    assert db.get(Warehouse, 7) == value
    root = Path("^YHMFTestWarehouse", (b"7",))
    assert db.store.get(root.child(b"address")) == b"o:1"
    assert db.store.get(root.child(b"address", b"city")) == b"s:Chicago"
    assert db.store.get(root.child(b"bins")) == b"c:1"
    assert (
        db.store.get(root.child(b"bins", b"s:A01", b"lots", b"s:L-001", b"quantity")) == b"d:12.50"
    )


def test_complete_storage_and_schema_fixtures(db):
    db.insert(warehouse())
    fixture_dir = FilePath(__file__).parent / "fixtures"
    expected = {
        tuple(part.encode("utf-8") for part in entry["subscripts"]): entry["value"].encode("utf-8")
        for entry in json.loads((fixture_dir / "warehouse.json").read_text())
    }
    schema = (fixture_dir / "warehouse-schema.json").read_bytes()
    expected[(b"7", b"%m", b"fingerprint")] = hashlib.sha256(schema).hexdigest().encode("ascii")
    actual = {}

    def visit(path):
        value = db.store.get(path)
        if value is not None:
            actual[path.subscripts] = value
        for key in db.store.children(path, limit=100):
            visit(path.child(key))

    visit(Path("^YHMFTestWarehouse"))
    assert actual == expected
    assert db.store.get(Path("^YHMFSchema", (b"YHMFTestWarehouse", b"snapshot"))) == schema


def test_null_empty_and_removed_subtrees_are_distinct(db):
    value = warehouse()
    db.insert(value)
    value.address = None
    value.bins["A01"].lots.clear()
    db.save(value)
    loaded = db.get(Warehouse, 7)
    assert loaded.address is None
    assert loaded.bins["A01"].lots == {}
    root = Path("^YHMFTestWarehouse", (b"7",))
    assert db.store.get(root.child(b"address")) == b"n:"
    assert db.store.get(root.child(b"address", b"city")) is None
    assert db.store.get(root.child(b"bins", b"s:A01", b"lots")) == b"c:0"
    assert db.store.children(root.child(b"bins", b"s:A01", b"lots"), limit=1) == ()
    value.bins.clear()
    db.save(value)
    assert db.get(Warehouse, 7).bins == {}
    assert db.store.get(root.child(b"bins")) == b"c:0"


def test_child_key_mismatch_cannot_replace_record(db):
    db.insert(warehouse())
    bad = warehouse()
    bad.bins["wrong"] = bad.bins.pop("A01")
    with pytest.raises(StorageError, match="identity"):
        db.save(bad)
    assert "A01" in db.get(Warehouse, 7).bins


def test_nested_unvalidated_values_cannot_bypass_constraints(db):
    value = warehouse()
    value.bins["A01"].lots["L-001"] = Lot(number="L-001").model_copy(
        update={"quantity": Decimal("-1")}
    )
    with pytest.raises(ValidationError):
        db.insert(value)
    with pytest.raises(NotFound):
        db.get(Warehouse, 7)


def test_collection_presence_and_count_are_checked(db):
    db.insert(warehouse())
    root = Path("^YHMFTestWarehouse", (b"7",))
    db.store.set(root.child(b"bins"), b"c:2")
    with pytest.raises(StorageError, match="collection"):
        db.get(Warehouse, 7)


def test_collection_size_is_bounded_before_writes(db):
    value = Warehouse(id=7, name="Large", bins={str(i): Bin(code=str(i)) for i in range(1001)})
    with pytest.raises(StorageError, match="1000"):
        db.insert(value)
    with pytest.raises(NotFound):
        db.get(Warehouse, 7)


def test_owned_models_cannot_claim_independent_globals():
    class Independent(Lot):
        __global__ = "^Independent"

    class Bad(Warehouse):
        lots: Children[Independent] = ChildField(key="number")

    with pytest.raises(SchemaError, match="independent"):
        Registry(Bad)


def test_child_field_must_use_the_declared_primary_key():
    class Bad(Warehouse):
        bins: Children[Bin] = ChildField(key="wrong")

    with pytest.raises(SchemaError, match="primary-key"):
        Registry(Bad)
