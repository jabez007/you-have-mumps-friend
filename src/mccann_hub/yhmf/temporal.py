# SPDX-License-Identifier: GPL-3.0-or-later
"""Version-one calendar dates and canonical UTC instants."""

from datetime import UTC, date, datetime

from .errors import SchemaError, StorageError


def encode(value: date | datetime) -> bytes:
    if type(value) is date:
        return b"D:" + value.isoformat().encode("ascii")
    if not isinstance(value, datetime):
        raise StorageError("Unsupported temporal type")
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError, TypeError):
        raise StorageError("Invalid datetime timezone") from None
    if offset is None:
        raise StorageError("Datetime values require a timezone")
    try:
        instant = value.astimezone(UTC)
    except (OverflowError, ValueError, TypeError):
        raise StorageError("Datetime UTC instant is outside the supported calendar") from None
    return b"T:" + (instant.isoformat(timespec="microseconds").removesuffix("+00:00") + "Z").encode(
        "ascii"
    )


def decode(tag: bytes, payload: bytes) -> date | datetime:
    text = payload.decode("ascii")
    return date.fromisoformat(text) if tag == b"D" else datetime.fromisoformat(text)


def literal(value: date | datetime) -> str:
    """Canonical metadata text, with declaration errors distinct from stored data errors."""
    try:
        return encode(value)[2:].decode("ascii")
    except StorageError:
        raise SchemaError(
            "Temporal defaults and bounds require representable aware instants"
        ) from None
