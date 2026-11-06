# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical snapshots and read-only inspection of installed schema metadata."""

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast

from .codecs import canonical_json as canonical_json
from .errors import SchemaMismatch, StorageError
from .maintenance import inspected_fingerprint
from .storage import MAX_VALUE_BYTES, Path, Store

if TYPE_CHECKING:
    from .registry import ModelSpec


def schema_path(global_name: str) -> Path:
    return Path("^YHMFSchema", (global_name[1:].encode("ascii"),))


def has_data(store: Store, root: Path) -> bool:
    """Include both a value at the root and any descendant, without scanning."""
    return store.get(root) is not None or bool(store.children(root, limit=1))


def install_schema(store: Store, spec: "ModelSpec") -> None:
    """Publish a validated declaration inside the caller's transaction."""
    if len(spec.snapshot) > MAX_VALUE_BYTES:
        raise StorageError("Schema snapshot exceeds the node limit")
    root = schema_path(spec.global_name)
    store.set(root, spec.fingerprint)
    store.set(root.child(b"snapshot"), spec.snapshot)


def check_schema(store: Store, spec: "ModelSpec", *, install: bool = False) -> None:
    """Verify exact metadata on each transaction attempt, with no local cache."""
    root = schema_path(spec.global_name)
    fingerprint = store.get(root)
    snapshot = store.get(root.child(b"snapshot"))
    if fingerprint is None:
        if snapshot is not None or store.children(root, limit=1):
            raise StorageError("Installed schema metadata is incomplete")
        if install:
            if has_data(store, Path(spec.global_name)):
                raise StorageError(
                    "Nonempty unmanaged global requires explicit adoption or migration"
                )
            install_schema(store, spec)
        return
    if fingerprint != spec.fingerprint:
        # Distinguish corrupted metadata from a valid, different declaration.
        inspect_schema(store, spec)
        raise SchemaMismatch("Installed schema differs; an explicit migration is required")
    if snapshot != spec.snapshot:
        raise StorageError("Installed schema snapshot does not match its registered fingerprint")


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
    migration_id: str | None = None

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
    fingerprint, migration_id = inspected_fingerprint(store, spec.global_name, store.get(root))
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
    return SchemaInspection(declared, installed, has_data(store, record_root), migration_id)
