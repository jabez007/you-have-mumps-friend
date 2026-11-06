# SPDX-License-Identifier: GPL-3.0-or-later
"""Version-one scalar and key encodings, independent of YDBPython."""

import base64
import binascii
import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import StorageError

type Scalar = str | int | bool | float | Decimal | bytes | None
type Key = str | int
LAYOUT_VERSION = 1
MAX_INTEGER_KEY = 999_999_999_999_999_999
_INTEGER = re.compile(rb"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")


def canonical_json(value: Any) -> bytes:
    """Encode deterministic, finite JSON for persisted mapper metadata."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise StorageError("Strings must contain valid UTF-8 text") from None


def encode_key(value: Key) -> bytes:
    """Retain integer collation and distinguish every string from an integer."""
    if type(value) is int and abs(value) <= MAX_INTEGER_KEY:
        return str(value).encode("ascii")
    if type(value) is str:
        return b"s:" + _utf8(value)
    raise StorageError("Keys must be strings or integers with at most 18 digits")


def decode_key(value: bytes) -> Key:
    """Reject noncanonical subscripts, including numeric aliases such as 01."""
    if value.startswith(b"s:"):
        try:
            return value[2:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageError("Invalid UTF-8 key") from exc
    if _INTEGER.fullmatch(value):
        number = int(value)
        if abs(number) <= MAX_INTEGER_KEY:
            return number
    raise StorageError("Invalid version-one key")


def encode_scalar(value: Scalar) -> bytes:
    """Encode one scalar without relying on its declared Python field type."""
    if value is None:
        return b"n:"
    if type(value) is str:
        return b"s:" + _utf8(value)
    if type(value) is bool:
        return b"b:1" if value else b"b:0"
    if type(value) is int:
        return b"i:" + str(value).encode("ascii")
    if type(value) is bytes:
        return b"x:" + base64.b64encode(value)
    if isinstance(value, Decimal) and value.is_finite():
        return b"d:" + str(value).encode("ascii")
    if type(value) is float and math.isfinite(value):
        return b"f:" + repr(value).encode("ascii")
    raise StorageError("Unsupported scalar type or nonfinite number")


def _decode_number(tag: bytes, payload: bytes) -> int | float | Decimal:
    if tag == b"i" and _INTEGER.fullmatch(payload):
        return int(payload)
    if tag == b"d":
        return Decimal(payload.decode("ascii"))
    if tag == b"f":
        return float(payload)
    raise StorageError("Invalid scalar number")


def decode_scalar(value: bytes) -> Scalar:
    """Reject corrupt, noncanonical, and future encodings rather than coercing."""
    tag, separator, payload = value.partition(b":")
    try:
        result: Scalar
        if tag == b"s":
            result = payload.decode("utf-8")
        elif tag == b"n":
            result = None
        elif tag == b"b":
            result = payload == b"1"
        elif tag == b"x":
            result = base64.b64decode(payload, validate=True)
        else:
            result = _decode_number(tag, payload)
        if not separator or encode_scalar(result) != value:
            raise StorageError("Noncanonical scalar encoding")
        return result
    except (ValueError, UnicodeError, InvalidOperation, binascii.Error) as exc:
        raise StorageError("Invalid version-one scalar") from exc
