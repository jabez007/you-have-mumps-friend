# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded concrete index locations, independent of root materialization."""

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from dataclasses import field as hidden
from typing import TYPE_CHECKING, Any

from . import indexes, references
from .codecs import Key, canonical_json, decode_key
from .errors import NotFound, QueryError, SchemaError, StorageError
from .models import Model, Reference
from .owned import OwnedPath
from .records import MAX_RECORD_NODES, value_nodes, walk_values
from .registry import FieldSpec, ModelSpec, reference_target
from .storage import Path, Store

if TYPE_CHECKING:
    from .repository import Database

_DIGEST = re.compile(rb"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Occurrence:
    root_key: Key = hidden(repr=False)
    at: OwnedPath = hidden(repr=False)
    field: str
    record: Model = hidden(repr=False)
    cursor: str = hidden(repr=False)


@dataclass(frozen=True)
class Address:
    at: OwnedPath
    declaration: tuple[str, ...]
    field: FieldSpec
    relative: tuple[bytes, ...]
    keyed_at: OwnedPath = ()
    keyed_relative: tuple[bytes, ...] = ()


def scope(spec: ModelSpec, prefix: Path) -> str:
    identity = [
        spec.fingerprint.decode("ascii"),
        prefix.global_name,
        [part.hex() for part in prefix.subscripts],
    ]
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def encode_cursor(identity: str, key: bytes, digest: bytes) -> str:
    raw = canonical_json([1, identity, key.hex(), digest.decode("ascii")])
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _cursor_data(token: str) -> tuple[str, str, str]:
    if not isinstance(token, str) or len(token) > 2048:
        raise QueryError("Invalid occurrence cursor")
    try:
        raw = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if base64.urlsafe_b64encode(raw).decode("ascii") != token or canonical_json(data) != raw:
            raise ValueError
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise QueryError("Invalid occurrence cursor") from None
    match data:
        case [1, str(identity), str(key), str(digest)] if type(data[0]) is int:
            return identity, key, digest
        case _:
            raise QueryError("Invalid occurrence cursor")


def decode_cursor(spec: ModelSpec, identity: str, token: str | None) -> tuple[bytes, bytes] | None:
    if token is None:
        return None
    saved, key, digest = _cursor_data(token)
    if saved != identity:
        raise QueryError("Occurrence cursor does not belong to this schema and query")
    try:
        raw, marker = bytes.fromhex(key), digest.encode("ascii")
        spec.path(decode_key(raw))
        if raw.hex() != key or not _DIGEST.fullmatch(marker):
            raise ValueError
    except (ValueError, SchemaError, StorageError):
        raise QueryError("Invalid occurrence cursor") from None
    return raw, marker


def entry_page(
    store: Store, prefix: Path, *, limit: int, after: tuple[bytes, bytes] | None
) -> tuple[Path, ...]:
    """Walk root keys and their digest leaves; never enumerate unrelated record trees."""
    if store.get(prefix) is not None:
        raise StorageError("Unexpected value at an occurrence namespace")
    keys = (after[0],) if after else store.children(prefix, limit=1)
    digest = after[1] if after else b""
    result: list[Path] = []
    while keys and len(result) < limit:
        key = keys[0]
        root = prefix.child(key)
        if store.get(root) is not None:
            raise StorageError("Unexpected value at an occurrence record prefix")
        markers = store.children(root, limit=limit - len(result), after=digest)
        result.extend(root.child(marker) for marker in markers)
        digest = b""
        keys = store.children(prefix, limit=1, after=key) if len(result) < limit else ()
    return tuple(result)


def _location(raw: bytes | None, digest: bytes) -> tuple[bytes, ...]:
    if raw is None or not _DIGEST.fullmatch(digest):
        raise StorageError("Invalid occurrence location or digest")
    try:
        data = json.loads(raw)
        if (
            not isinstance(data, list)
            or not 1 <= len(data) <= 30
            or not all(isinstance(part, str) for part in data)
            or canonical_json(data) != raw
            or hashlib.sha256(raw).hexdigest().encode("ascii") != digest
        ):
            raise ValueError
        return tuple(part.encode("utf-8") for part in data)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise StorageError("Invalid occurrence location or digest") from None


def _child_key(field: FieldSpec, parts: tuple[bytes, ...], index: int) -> Key:
    if index >= len(parts):
        raise StorageError("Occurrence location is missing a child key")
    assert field.node is not None and field.node.key_field is not None
    key = decode_key(parts[index])
    if type(key) is not field.node.model.model_fields[field.node.key_field].annotation:
        raise StorageError("Occurrence child key disagrees with its declaration")
    return key


def address(spec: ModelSpec, raw: bytes | None, digest: bytes) -> Address:
    parts = _location(raw, digest)
    fields, index = spec.fields, 0
    at: list[Key] = []
    names: list[str] = []
    keyed_at: OwnedPath = ()
    keyed_relative: tuple[bytes, ...] = ()
    while index < len(parts):
        selected = next(
            (item for item in fields if item.name.encode("ascii") == parts[index]), None
        )
        if selected is None:
            raise StorageError("Occurrence location names an undeclared field")
        index += 1
        names.append(selected.name)
        if selected.node is None:
            if index != len(parts):
                raise StorageError("Occurrence location descends through a scalar field")
            return Address(tuple(at), tuple(names), selected, parts, keyed_at, keyed_relative)
        at.append(selected.name)
        if selected.kind == "children":
            at.append(_child_key(selected, parts, index))
            index += 1
            keyed_at, keyed_relative = tuple(at), parts[:index]
        fields = selected.node.fields
    raise StorageError("Occurrence location must identify a declared leaf field")


def read_address(tx: "Database", spec: ModelSpec, key: Key, location: Address) -> Model:
    try:
        record = tx.get(spec.model, key, at=location.at)
    except NotFound:
        raise StorageError("Occurrence points to a missing owner") from None
    except SchemaError:
        raise StorageError("Occurrence root key disagrees with its declaration") from None
    check_address_value(tx.store, spec, key, location, record)
    return record


def check_address_value(
    store: Store, spec: ModelSpec, key: Key, location: Address, record: Model
) -> None:
    """Verify stored field bytes against an already validated owner in this attempt."""
    encoded = value_nodes(
        location.field,
        spec.path(key).child(*location.relative),
        getattr(record, location.field.name),
    )
    if any(store.get(path) != value for path, value in encoded.items()):
        raise StorageError("Validated occurrence differs from its stored field")


def _indexed(
    tx: "Database",
    spec: ModelSpec,
    definition: indexes.Definition,
    expected: bytes,
    identity: str,
    path: Path,
) -> Occurrence:
    raw = tx.store.get(path)
    if tx.store.children(path, limit=1):
        raise StorageError("An occurrence leaf has unexpected descendants")
    location = address(spec, raw, path.subscripts[-1])
    if location.declaration != definition.path or location.field is not definition.field:
        raise StorageError("Occurrence location disagrees with the queried declaration")
    key = decode_key(path.subscripts[-2])
    record = read_address(tx, spec, key, location)
    value = getattr(record, location.field.name)
    if indexes.normalized(value) != expected:
        raise StorageError("Index value does not match its concrete occurrence")
    entry = next(
        iter(
            indexes.entries_at(spec, spec.path(key), ((location.field, location.relative, value),))
        )
    )
    if entry.path != path or entry.location != raw:
        raise StorageError("Index occurrence identity does not match its record")
    if entry.claim_path is not None and tx.store.get(entry.claim_path) != entry.claim:
        raise StorageError("Unique claim does not match its indexed occurrence")
    cursor = encode_cursor(identity, path.subscripts[-2], path.subscripts[-1])
    return Occurrence(key, location.at, definition.name, record, cursor)


def page(
    tx: "Database",
    spec: ModelSpec,
    definition: indexes.Definition,
    value: Any,
    *,
    limit: int,
    after: str | None,
) -> list[Occurrence]:
    spec = tx._check_schema(spec)
    prefix = indexes.index_root(spec, definition, value)
    identity = scope(spec, prefix)
    cursor = decode_cursor(spec, identity, after)
    paths = entry_page(tx.store, prefix, limit=limit, after=cursor)
    expected = indexes.normalized(value)
    return [_indexed(tx, spec, definition, expected, identity, path) for path in paths]


def validate_root(
    tx: "Database",
    spec: ModelSpec,
    definition: indexes.Definition,
    value: Any,
    key: bytes,
    record: Model,
) -> None:
    """Validate the concrete equality leaves even when another child also matches."""
    expected_value = indexes.normalized(value)
    matches = (
        (field, path, stored)
        for field, path, stored in walk_values(spec.fields, record)
        if field is definition.field and indexes.normalized(stored) == expected_value
    )
    root = spec.path(decode_key(key))
    entries = indexes.entries_at(spec, root, matches)
    prefix = indexes.index_root(spec, definition, value).child(key)
    actual = tx.store.children(prefix, limit=MAX_RECORD_NODES)
    if (
        tx.store.get(prefix) is not None
        or not entries
        or {entry.path.subscripts[-1] for entry in entries} != set(actual)
    ):
        raise StorageError("Index occurrence set does not match its owning record")
    for entry in entries:
        if tx.store.get(entry.path) != entry.location or tx.store.children(entry.path, limit=1):
            raise StorageError("Index occurrence location is missing or corrupt")


def _inverse(
    tx: "Database",
    spec: ModelSpec,
    target_model: type[Model],
    target: Path,
    identity: str,
    path: Path,
) -> Occurrence:
    raw = tx.store.get(path)
    if tx.store.children(path, limit=1):
        raise StorageError("A reverse occurrence leaf has unexpected descendants")
    location = address(spec, raw, path.subscripts[-1])
    if location.field.target is not target_model:
        raise StorageError("Reverse occurrence names a different reference declaration")
    key = decode_key(path.subscripts[-2])
    record = read_address(tx, spec, key, location)
    value = getattr(record, location.field.name)
    if not isinstance(value, Reference):
        raise StorageError("Reverse occurrence does not contain its target reference")
    link = next(
        iter(
            references.links_at(
                tx.registry, spec, spec.path(key), ((location.field, location.relative, value),)
            )
        )
    )
    if link.target != target or link.index_path != path or link.location != raw:
        raise StorageError("Reverse occurrence disagrees with its exact source field")
    cursor = encode_cursor(identity, path.subscripts[-2], path.subscripts[-1])
    return Occurrence(key, location.at, ".".join(location.declaration), record, cursor)


def inverse_page(
    tx: "Database", reference: Reference[Any], source: type[Model], *, limit: int, after: str | None
) -> list[Occurrence]:
    if not isinstance(reference, Reference):
        raise SchemaError("Inverse reads require a concrete Reference target")
    target_model = reference_target(type(reference))
    target_spec = tx._check_schema(tx.registry.spec(target_model))
    target = target_spec.path(reference.key)
    if not tx._exists(target_spec, target):
        raise NotFound("Reference target does not exist")
    spec = tx._check_schema(tx.registry.spec(source))
    root = references.reference_root(target)
    count = references.inbound_count(tx.store, root)
    prefix = root.child(spec.global_name[1:].encode("ascii"))
    identity = scope(spec, prefix)
    cursor = decode_cursor(spec, identity, after)
    paths = entry_page(tx.store, prefix, limit=limit, after=cursor)
    if len(paths) > count:
        raise StorageError("Reverse occurrence page exceeds the stored inbound count")
    return [_inverse(tx, spec, target_model, target, identity, path) for path in paths]
