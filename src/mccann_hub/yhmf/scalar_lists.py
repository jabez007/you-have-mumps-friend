# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical length markers for bounded, numbered scalar lists."""

from .errors import StorageError

MAX_ITEMS = 1000


def length(marker: bytes | None) -> int:
    try:
        if marker is None or not marker.startswith(b"l:"):
            raise ValueError
        count = int(marker[2:])
        if not 0 <= count <= MAX_ITEMS or marker != b"l:" + str(count).encode("ascii"):
            raise ValueError
        return count
    except ValueError as exc:
        raise StorageError("Invalid or oversized scalar list marker") from exc
