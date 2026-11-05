# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical snapshots and read-only inspection of installed schema metadata."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast

from .errors import StorageError
from .storage import MAX_VALUE_BYTES, Path, Store

if TYPE_CHECKING:
    from .registry import ModelSpec


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def schema_path(global_name: str) -> Path:
    return Path("^YHMFSchema", (global_name[1:].encode("ascii"),))


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Nonfinite JSON constants are not canonical")


def _snapshot_document(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or len(data) > MAX_VALUE_BYTES:
        raise StorageError("Schema snapshot must be bytes within the node limit")
    try:
        document = json.loads(data.decode("ascii"), parse_constant=_reject_constant)
        if not isinstance(document, dict) or canonical_json(document) != data:
            raise ValueError("Snapshot is not a canonical JSON object")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StorageError("Malformed canonical schema snapshot") from exc
    _validate_header(document)
    return document


def _validate_header(document: dict[str, Any]) -> None:
    global_name = document.get("global")
    if not isinstance(global_name, str):
        raise StorageError("Schema snapshot has no valid global")
    Path(global_name)
    for name in ("layout", "version"):
        value = document.get(name)
        if type(value) is not int or value < 1:
            raise StorageError("Schema snapshot versions must be positive integers")
    key = document.get("key")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
        raise StorageError("Schema snapshot has no valid primary-key field")
    if not isinstance(document.get("declaration"), dict):
        raise StorageError("Schema snapshot has no model declaration")


@dataclass(frozen=True)
class SchemaSnapshot:
    """Immutable, validated canonical bytes. Full defaults require explicit access."""

    canonical: bytes = field(repr=False)
    fingerprint: str = field(init=False)
    global_name: str = field(init=False)
    layout_version: int = field(init=False)
    schema_version: int = field(init=False)
    primary_key: str = field(init=False)

    def __post_init__(self) -> None:
        document = _snapshot_document(self.canonical)
        object.__setattr__(self, "fingerprint", hashlib.sha256(self.canonical).hexdigest())
        object.__setattr__(self, "global_name", document["global"])
        object.__setattr__(self, "layout_version", document["layout"])
        object.__setattr__(self, "schema_version", document["version"])
        object.__setattr__(self, "primary_key", document["key"])

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh JSON document, including declared default values."""
        return cast(dict[str, Any], json.loads(self.canonical))


@dataclass(frozen=True)
class SchemaInspection:
    declared: SchemaSnapshot
    installed: SchemaSnapshot | None
    has_data: bool

    @property
    def status(self) -> Literal["uninstalled", "unmanaged", "matching", "different"]:
        if self.installed is None:
            return "unmanaged" if self.has_data else "uninstalled"
        return (
            "matching" if self.installed.fingerprint == self.declared.fingerprint else "different"
        )


def inspect_schema(store: Store, spec: "ModelSpec") -> SchemaInspection:
    declared = SchemaSnapshot(spec.snapshot)
    root = schema_path(spec.global_name)
    fingerprint = store.get(root)
    data = store.get(root.child(b"snapshot"))
    installed = None
    if fingerprint is not None or data is not None:
        if fingerprint is None or data is None:
            raise StorageError("Installed schema metadata is incomplete")
        installed = SchemaSnapshot(data)
        if installed.global_name != spec.global_name:
            raise StorageError("Installed snapshot global does not match its location")
        if fingerprint != installed.fingerprint.encode("ascii"):
            raise StorageError("Installed schema fingerprint does not match its snapshot")
    record_root = Path(spec.global_name)
    has_data = store.get(record_root) is not None or bool(store.children(record_root, limit=1))
    return SchemaInspection(declared, installed, has_data)
