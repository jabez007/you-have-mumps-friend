# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.errors import (
    NotFound,
    ReferenceViolation,
    SchemaError,
    StorageError,
    TransactionError,
)


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str


class Line(Model):
    number: str = Field(primary_key=True)
    product: Reference[Product]


class Details(Model):
    alternate: Reference[Product] | None = None


class Order(Model):
    __global__ = "^YHMFTestOrder"
    id: int = Field(primary_key=True)
    product: Reference[Product] | None = Field(default=None, on_delete="restrict")
    details: Details = Field(default_factory=Details)
    lines: Children[Line] = ChildField(key="number")


@pytest.fixture
def db(store):
    return Database(store, Registry(Order, Product))


def ref(key="P42"):
    return Reference[Product](key=key)


def inbound(db, key="P42"):
    return db.store.get(Path("^YHMFRef", (b"YHMFTestProduct", f"s:{key}".encode())))


def test_reference_roundtrip_and_restrict_delete(db):
    db.insert(Product(id="P42", name="Main"))
    db.insert(Order(id=1, product=ref()))
    loaded = db.get(Order, 1)
    assert loaded.product == ref()
    assert db.store.get(Path("^YHMFTestOrder", (b"1", b"product"))) == b"r:s:P42"
    assert inbound(db) == b"1"
    with pytest.raises(ReferenceViolation, match="still referenced"):
        db.delete(Product, "P42")
    assert db.get(Product, "P42").name == "Main"
    assert db.delete(Order, 1)
    assert inbound(db) == b"0"
    assert db.delete(Product, "P42")
    assert inbound(db) is None


def test_missing_target_rolls_back_record_and_schema(db):
    with pytest.raises(ReferenceViolation, match="does not exist"):
        db.insert(Order(id=1, product=ref()))
    with pytest.raises(NotFound):
        db.get(Order, 1)
    assert inbound(db) is None
    assert db.store.get(Path("^YHMFSchema", (b"YHMFTestOrder",))) is None


def test_optional_reference_accepts_null_but_still_restricts_when_set(db):
    db.insert(Order(id=1))
    assert db.get(Order, 1).product is None
    assert inbound(db) is None
    db.insert(Product(id="P42", name="Main"))
    db.save(Order(id=1, product=ref()))
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P42")
    db.save(Order(id=1))
    assert inbound(db) == b"0"
    assert db.delete(Product, "P42")


def test_reference_replacement_releases_old_target(db):
    db.insert(Product(id="P42", name="First"))
    db.insert(Product(id="P43", name="Second"))
    db.insert(Order(id=1, product=ref()))
    db.save(Order(id=1, product=ref("P43")))
    assert inbound(db, "P42") == b"0"
    assert inbound(db, "P43") == b"1"
    assert db.delete(Product, "P42")
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P43")


def test_reference_locations_include_owned_child_and_embedded_paths(db):
    db.insert(Product(id="P42", name="Main"))
    db.insert(
        Order(
            id=1, details=Details(alternate=ref()), lines={"L1": Line(number="L1", product=ref())}
        )
    )
    assert inbound(db) == b"2"
    location = b'["lines","s:L1","product"]'
    digest = hashlib.sha256(location).hexdigest().encode("ascii")
    index_path = Path("^YHMFRef", (b"YHMFTestProduct", b"s:P42", b"YHMFTestOrder", b"1", digest))
    assert db.store.get(index_path) == location
    db.save(Order(id=1))
    assert db.store.get(index_path) is None
    assert inbound(db) == b"0"
    assert db.delete(Product, "P42")


def test_saving_unchanged_references_does_not_double_count(db):
    db.insert(Product(id="P42", name="Main"))
    order = Order(id=1, product=ref())
    db.insert(order)
    db.save(order)
    assert inbound(db) == b"1"
    db.save(Product(id="P42", name="Updated"))
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P42")


def test_failed_reference_replacement_preserves_old_record_and_index(db):
    db.insert(Product(id="P42", name="Main"))
    db.insert(Order(id=1, product=ref()))
    with pytest.raises(ReferenceViolation):
        db.save(Order(id=1, product=ref("missing")))
    assert db.get(Order, 1).product == ref()
    assert inbound(db) == b"1"
    assert inbound(db, "missing") is None


def test_creating_target_then_source_in_one_transaction(db):
    def create(tx):
        tx.insert(Product(id="P42", name="Main"))
        return tx.insert(Order(id=1, product=ref()))

    assert db.atomic(create).product == ref()
    assert inbound(db) == b"1"


def test_deleting_source_then_target_in_one_transaction(db):
    db.insert(Product(id="P42", name="Main"))
    db.insert(Order(id=1, product=ref()))

    def remove(tx):
        assert tx.delete(Order, 1)
        assert tx.delete(Product, "P42")

    db.atomic(remove)
    assert inbound(db) is None


def test_caught_reference_failure_cannot_commit_partial_source(db):
    def create(tx):
        try:
            tx.insert(Order(id=1, product=ref("missing")))
        except ReferenceViolation:
            pass

    with pytest.raises(TransactionError):
        db.atomic(create)
    with pytest.raises(NotFound):
        db.get(Order, 1)


def test_corrupt_reference_index_prevents_unsafe_delete(db):
    db.insert(Product(id="P42", name="Main"))
    db.insert(Order(id=1, product=ref()))
    db.store.delete_tree(Path("^YHMFRef", (b"YHMFTestProduct", b"s:P42")))
    with pytest.raises(StorageError, match="reverse-reference"):
        db.delete(Order, 1)
    assert db.get(Order, 1).product == ref()


def test_wrong_reference_key_type_is_rejected_before_writes(db):
    with pytest.raises(SchemaError, match="Key type"):
        db.insert(Order(id=1, product=ref(42)))
    with pytest.raises(NotFound):
        db.get(Order, 1)


def test_reference_is_immutable():
    value = ref()
    with pytest.raises(ValidationError):
        value.key = "P43"


def test_reference_target_must_be_registered():
    with pytest.raises(SchemaError, match="Register"):
        Registry(Order)


def test_reference_snapshot_records_target_and_policy(db):
    declaration = json.loads(db.registry.spec(Order).snapshot)
    assert {
        "path": ["lines", "product"],
        "global": "^YHMFTestProduct",
        "key": "id",
        "on_delete": "restrict",
    } in declaration["references"]


@pytest.mark.parametrize("policy", ["set_null", "cascade", "ignore"])
def test_unsupported_delete_policies_fail_explicitly(policy):
    with pytest.raises(NotImplementedError, match="restrict"):
        Field(on_delete=policy)


def test_reference_policy_cannot_be_applied_to_scalar():
    class Bad(Product):
        name: str = Field(on_delete="restrict")

    with pytest.raises(SchemaError, match="Reference"):
        Registry(Bad)


def test_self_reference_is_valid_and_does_not_block_its_own_deletion(store):
    class Node(Model):
        __global__ = "^YHMFTestOrder"
        id: int = Field(primary_key=True)
        parent: "Reference[Node] | None" = None

    Node.model_rebuild(_types_namespace={"Node": Node})
    db = Database(store, Registry(Node))
    db.insert(Node(id=1, parent=Reference[Node](key=1)))
    assert db.get(Node, 1).parent.key == 1
    db.insert(Node(id=2, parent=Reference[Node](key=1)))
    with pytest.raises(ReferenceViolation):
        db.delete(Node, 1)
    assert db.delete(Node, 2)
    assert db.delete(Node, 1)


def test_corrupt_inbound_count_refuses_deletion(db):
    db.insert(Product(id="P42", name="Main"))
    db.store.set(Path("^YHMFRef", (b"YHMFTestProduct", b"s:P42")), b"1")
    with pytest.raises(StorageError, match="disagree"):
        db.delete(Product, "P42")
    assert db.get(Product, "P42").name == "Main"


def test_abort_restores_record_and_reference_index_together(db):
    db.insert(Product(id="P42", name="Main"))

    def create(tx):
        tx.insert(Order(id=1, product=ref()))
        raise RuntimeError("abort after reverse writes")

    with pytest.raises(RuntimeError, match="abort after"):
        db.atomic(create)
    assert inbound(db) is None
    with pytest.raises(NotFound):
        db.get(Order, 1)
    db.insert(Order(id=1, product=ref()))

    def remove(tx):
        tx.delete(Order, 1)
        raise RuntimeError("abort after reverse deletion")

    with pytest.raises(RuntimeError, match="abort after"):
        db.atomic(remove)
    assert inbound(db) == b"1"
    assert db.get(Order, 1).product == ref()
