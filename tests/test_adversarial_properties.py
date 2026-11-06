# SPDX-License-Identifier: GPL-3.0-or-later
"""Boundary and representation properties beyond the existing scalar goldens."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import create_model

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    Path,
    Registry,
)
from mccann_hub.yhmf.codecs import decode_scalar, encode_key, encode_scalar
from mccann_hub.yhmf.errors import StorageError
from mccann_hub.yhmf.storage import MAX_VALUE_BYTES

pytestmark = pytest.mark.adversarial
unicode_text = st.text(alphabet=st.characters(codec="utf-8"), max_size=30)
keys = unicode_text.filter(lambda value: "\0" not in value)
decimals = st.decimals(
    min_value=0, max_value=10**6, places=6, allow_nan=False, allow_infinity=False
)


class Item(Model):
    id: int = Field(primary_key=True)
    quantity: Decimal = Field(ge=0)
    note: str | None
    data: bytes


class Aggregate(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    children: Children[Item] | None = ChildField(key="id")


@given(
    keys,
    st.one_of(
        st.none(),
        st.dictionaries(
            st.integers(-20, 20),
            st.tuples(decimals, st.one_of(st.none(), unicode_text), st.binary(max_size=30)),
            max_size=4,
        ),
    ),
)
@settings(deadline=None, print_blob=True)
def test_nested_model_roundtrip_preserves_null_empty_unicode_bytes_and_decimal_scale(key, values):
    children = (
        None
        if values is None
        else {
            number: Item(id=number, quantity=value[0], note=value[1], data=value[2])
            for number, value in values.items()
        }
    )
    value = Aggregate(id=key, children=children)
    db = Database(MemoryStore(), Registry(Aggregate))
    db.insert(value)
    actual = db.get(Aggregate, key)
    assert actual == value
    if children:
        for number, child in children.items():
            assert actual.children[number].quantity.as_tuple() == child.quantity.as_tuple()
    root = Registry(Aggregate).spec(Aggregate).path(key)
    db.store.delete_tree(root.child(b"id"))
    with pytest.raises(StorageError):
        db.get(Aggregate, key)  # Missing persisted values never become defaults.


@given(st.permutations(["id", "value", "note"]), unicode_text)
def test_generated_schema_order_and_presentation_do_not_change_identity(order, description):
    fields = {
        "id": (str, Field(primary_key=True)),
        "value": (Decimal, Field(default=Decimal("0.00"), ge=0)),
        "note": (str | None, Field(default=None, description=description)),
    }
    first = create_model("First", __base__=Model, **fields)
    second = create_model("Renamed", __base__=Model, **{name: fields[name] for name in order})
    first.__global__ = second.__global__ = "^YHMFTestProduct"
    assert Registry(first).spec(first).snapshot == Registry(second).spec(second).snapshot


@given(st.integers(-(10**12), 10**12))
def test_numeric_keys_and_their_string_spellings_are_distinct(number):
    assert encode_key(number) != encode_key(str(number))
    store = MemoryStore()
    root = Path("^YHMFTestStorage")
    store.set(root.child(encode_key(number)), b"integer")
    store.set(root.child(encode_key(str(number))), b"string")
    assert store.children(root, limit=3) == (encode_key(number), encode_key(str(number)))


@given(st.integers(29, 33), st.integers(-2, 2))
def test_depth_and_conservative_key_size_boundaries(depth, offset):
    if depth > 31:
        with pytest.raises(StorageError, match="31"):
            Path("^YHMFTestStorage", (b"k",) * depth)
    else:
        assert len(Path("^YHMFTestStorage", (b"k",) * depth).subscripts) == depth
    root = Path("^YHMFTestStorage")
    maximum = (1019 - len(root.global_name) - 4) // 2
    if offset > 0:
        with pytest.raises(StorageError, match="key-size"):
            root.child(b"x" * (maximum + offset))
    else:
        assert root.child(b"x" * (maximum + offset))


@given(st.integers(MAX_VALUE_BYTES - 3, MAX_VALUE_BYTES + 3))
def test_node_value_limit_is_checked_before_storage(length):
    store, root = MemoryStore(), Path("^YHMFTestStorage")
    store.set(root, b"before")
    if length > MAX_VALUE_BYTES:
        with pytest.raises(StorageError):
            store.set(root, b"x" * length)
        assert store.get(root) == b"before"
    else:
        store.set(root, b"x" * length)
        assert store.get(root) == b"x" * length


@given(st.binary(max_size=40))
def test_arbitrary_scalar_bytes_either_decode_canonically_or_fail(raw):
    try:
        value = decode_scalar(raw)
    except StorageError:
        return
    assert encode_scalar(value) == raw


@pytest.mark.parametrize("operation", [encode_scalar, encode_key])
def test_unpaired_unicode_surrogates_raise_mapper_errors(operation):
    with pytest.raises(StorageError, match="UTF-8"):
        operation("\ud800")
