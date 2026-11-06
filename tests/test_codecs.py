# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mccann_hub.yhmf.codecs import decode_key, decode_scalar, encode_key, encode_scalar
from mccann_hub.yhmf.errors import StorageError


def test_golden_scalars():
    fixtures = json.loads((Path(__file__).parent / "fixtures/scalars.json").read_text())
    conversions = {"decimal": Decimal, "bytes": bytes.fromhex}
    for case in fixtures:
        value = conversions.get(case["kind"], lambda v: v)(case["value"])
        encoded = case["encoded"].encode("utf-8")
        assert encode_scalar(value) == encoded
        decoded = decode_scalar(encoded)
        assert type(decoded) is type(value)
        assert decoded == value
        assert encode_scalar(decoded) == encoded


@given(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(),
        st.binary(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.decimals(allow_nan=False, allow_infinity=False),
    )
)
def test_scalar_roundtrip(value):
    assert encode_scalar(decode_scalar(encode_scalar(value))) == encode_scalar(value)


@given(st.one_of(st.text(), st.integers(min_value=-(10**18 - 1), max_value=10**18 - 1)))
def test_key_roundtrip(value):
    assert decode_key(encode_key(value)) == value


def test_keys_preserve_type_and_native_numeric_order():
    assert encode_key(7) == b"7"
    assert encode_key("7") == b"s:7"
    assert encode_key("") == b"s:"


@pytest.mark.parametrize("value", [True, 10**18, -(10**18), None, 1.5])
def test_reject_invalid_keys(value):
    with pytest.raises(StorageError):
        encode_key(value)


@pytest.mark.parametrize("value", [b"01", b"-0", b"+1", b"1.0", b"s:\xff", b"1000000000000000000"])
def test_reject_noncanonical_key(value):
    with pytest.raises(StorageError):
        decode_key(value)


@pytest.mark.parametrize(
    "value",
    [b"", b"n:x", b"b:2", b"i:01", b"f:nan", b"d:NaN", b"f:1", b"x:*", b"s:\xff", b"future:1"],
)
def test_reject_corrupt_scalar(value):
    with pytest.raises(StorageError):
        decode_scalar(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN"), [], {}])
def test_reject_unsupported_scalars(value):
    with pytest.raises(StorageError):
        encode_scalar(value)


@pytest.mark.parametrize("sign", [-1, 1])
def test_integer_beyond_interpreter_conversion_limit_has_a_storage_error(sign):
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        with pytest.raises(StorageError) as error:
            encode_scalar(sign * 10**5000)
        assert isinstance(error.value, ValueError)
        assert "5000" not in str(error.value)
    finally:
        sys.set_int_max_str_digits(previous)


def test_oversized_numeric_key_never_escapes_as_plain_value_error():
    with pytest.raises(StorageError):
        decode_key(b"1" * 5000)
