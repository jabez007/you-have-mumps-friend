# SPDX-License-Identifier: GPL-3.0-or-later
"""Ordered-v1 index subscripts; record scalar codecs remain unchanged."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any

from .codecs import encode_scalar
from .errors import StorageError

if TYPE_CHECKING:
    from .choices import Choices


def supports(annotation: Any, choices: "Choices | None") -> bool:
    return annotation in {str, int, Decimal, date, datetime} or (
        choices is not None and type(choices.values[0]) in (str, int)
    )


def _numeric(value: int | Decimal) -> bytes:
    number = Decimal(value)
    if not number.is_finite():
        raise StorageError("Ordered numeric indexes require finite values")
    sign, digits, exponent = number.as_tuple()
    coefficient = "".join(map(str, digits)).rstrip("0")
    if not coefficient:
        return b"0"
    power = int(exponent) + len(digits) - len(coefficient)
    adjusted = len(coefficient) + power - 1
    if len(coefficient) > 18 or not -18 <= adjusted < 18:
        raise StorageError(
            "ordered-v1 numeric values require at most 18 significant digits "
            "and nonzero magnitude from 1E-18 to below 1E18"
        )
    point = len(coefficient) + power
    if point <= 0:
        payload = "." + "0" * (-point) + coefficient
    elif point >= len(coefficient):
        payload = coefficient + "0" * (point - len(coefficient))
    else:
        payload = coefficient[:point] + "." + coefficient[point:]
    return ("-" if sign else "").encode("ascii") + payload.encode("ascii")


def value_key(value: Any) -> bytes:
    """Preserve native numeric or lexical order without rounding or numeric aliases."""
    if isinstance(value, Enum):
        value = value.value
    if value is None:
        return b"z:null"
    if type(value) is int or isinstance(value, Decimal):
        return _numeric(value)
    if type(value) in (date, datetime):
        return encode_scalar(value)
    if type(value) is str:
        payload = encode_scalar(value)[2:]
        return b"s:" + payload.replace(b"\x01", b"\x01\x02").replace(b"\x00", b"\x01\x01")
    raise StorageError("ordered-v1 supports str, int, Decimal, date, or datetime scalar values")
