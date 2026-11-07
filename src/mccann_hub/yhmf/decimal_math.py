# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded exact 1E-18 units shared by stored sums and native writer contracts."""

from decimal import Decimal
from typing import cast

from .errors import StorageError
from .storage import MAX_VALUE_BYTES


def units(value: Decimal, *, source: bool = False) -> int:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise StorageError("Stored sums require finite Decimal values")
    sign, digits, exponent = value.as_tuple()
    if len(digits) > MAX_VALUE_BYTES:
        raise StorageError("Stored sum input exceeds its encoded size domain")
    coefficient = "".join(map(str, digits)).rstrip("0")
    if not coefficient:
        return 0
    power = int(exponent) + len(digits) - len(coefficient)
    if source and (len(coefficient) > 18 or len(coefficient) + power > 18):
        raise StorageError(
            "decimal-sum-v1 inputs require 18 significant digits and magnitude below 1E18"
        )
    if power < -18 or len(coefficient) + power + 18 > 40:
        raise StorageError(
            "decimal-sum-v1 requires at most 18 fractional digits and 40 aggregate unit digits"
        )
    return (-1 if sign else 1) * int(coefficient) * cast(int, 10 ** (power + 18))


def from_units(units: int) -> Decimal:
    text = str(abs(units))
    if len(text) > 40:
        raise StorageError("Stored sum exceeds its bounded aggregate domain")
    coefficient = text.rstrip("0")
    if not coefficient:
        return Decimal(0)
    return Decimal(
        (int(units < 0), tuple(map(int, coefficient)), -18 + len(text) - len(coefficient))
    )
