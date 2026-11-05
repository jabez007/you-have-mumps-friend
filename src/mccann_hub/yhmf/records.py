# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate validated record values to native nodes and reconstruct stored fields."""

from typing import Any, cast

from .codecs import LAYOUT_VERSION, Scalar, decode_scalar, encode_scalar
from .errors import StorageError
from .models import Model
from .registry import ModelSpec
from .storage import MAX_VALUE_BYTES, Path, Store


def metadata(spec: ModelSpec) -> dict[bytes, bytes]:
    return {
        b"layout": str(LAYOUT_VERSION).encode("ascii"),
        b"schema": str(spec.model.__schema_version__).encode("ascii"),
        b"type": spec.global_name[1:].encode("ascii"),
        b"fingerprint": spec.fingerprint,
    }


def record_nodes(spec: ModelSpec, root: Path, record: Model) -> dict[Path, bytes]:
    nodes = {root.child(b"%m", name): value for name, value in metadata(spec).items()}
    for name in spec.fields:
        value = cast(Scalar, getattr(record, name))
        nodes[root.child(name.encode("ascii"))] = encode_scalar(value)
    if any(len(value) > MAX_VALUE_BYTES for value in nodes.values()):
        raise StorageError("Record contains a value exceeding the node limit")
    return nodes


def record_values(spec: ModelSpec, root: Path, store: Store) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in spec.fields:
        stored = store.get(root.child(name.encode("ascii")))
        if stored is None:
            raise StorageError("A declared field is missing from storage")
        values[name] = decode_scalar(stored)
    return values
