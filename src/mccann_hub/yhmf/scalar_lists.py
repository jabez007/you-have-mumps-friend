# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical length markers for bounded, numbered scalar lists."""

import types
from typing import Any, Union, get_args, get_origin

from pydantic.fields import FieldInfo

from .errors import StorageError

MAX_ITEMS = 1000


def item_declaration(annotation: Any) -> tuple[FieldInfo, bool]:
    """Peel nullable/Annotated wrappers while retaining scalar validation rules."""
    declaration, nullable = FieldInfo.from_annotation(annotation), False
    while get_origin(declaration.annotation) in (Union, types.UnionType):
        options = get_args(declaration.annotation)
        nonnull = [item for item in options if item is not type(None)]
        if len(nonnull) != 1 or len(nonnull) == len(options):
            break
        nullable = True
        inner = FieldInfo.from_annotation(nonnull[0])
        declaration = FieldInfo.merge_field_infos(inner, declaration, annotation=inner.annotation)
    return declaration, nullable


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
