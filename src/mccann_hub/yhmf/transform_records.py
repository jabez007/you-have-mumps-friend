# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated source/target values and reproducible transform record digests."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from . import indexes, migration_claims, references
from .codecs import canonical_json, decode_key
from .errors import MigrationError, SchemaMismatch, StorageError
from .maintenance import MigrationStatus, job_path
from .migration_records import check_metadata, field_nodes, validate_target_metadata
from .models import Model
from .records import input_values, metadata, record_values
from .registry import ModelSpec, Registry, reference_fields
from .schema_compatibility import bind_snapshot
from .schemas import SchemaSnapshot
from .storage import Path, Store
from .transforms import Transform


@dataclass(frozen=True)
class Binding:
    source: ModelSpec
    target: ModelSpec
    source_registry: Registry
    registry: Registry
    transform: Transform[Any, Any]


def bind(
    registry: Registry,
    source: type[Model],
    target: type[Model],
    transform: Transform[Any, Any],
    source_snapshot: SchemaSnapshot,
    target_snapshot: SchemaSnapshot,
) -> Binding:
    old, new = Registry().register(source), registry.spec(target)
    try:
        old, new = bind_snapshot(old, source_snapshot), bind_snapshot(new, target_snapshot)
    except SchemaMismatch:
        raise MigrationError(
            "Transform declarations differ from the saved schema bindings"
        ) from None
    if (
        old.global_name != new.global_name
        or old.key_field != new.key_field
        or source.model_fields[old.key_field].annotation
        is not target.model_fields[new.key_field].annotation
    ):
        raise MigrationError("Transforms must preserve the root global and primary-key identity")
    # Only these compiled path bindings are used for historical reverse edges.
    # New target existence/schema checks always use the live target registry.
    historical = Registry()
    historical.register(source)
    for field in reference_fields(old.fields):
        assert field.target is not None
        historical.register(field.target)
    return Binding(old, new, historical, registry, transform)


def _read(
    store: Store, spec: ModelSpec, metadata_spec: ModelSpec, root: Path
) -> tuple[Model, dict[Path, bytes]]:
    check_metadata(store, metadata_spec, root)
    try:
        result = spec.model.model_validate(record_values(spec, root, store))
    except ValidationError:
        raise MigrationError("Stored data does not satisfy the transform binding") from None
    if spec.path(getattr(result, spec.key_field)) != root:
        raise MigrationError("Transform validation changed a record identity")
    nodes = field_nodes(spec, root, result)
    if any(store.get(path) != value for path, value in nodes.items()):
        raise MigrationError("Transform binding validation would change stored values")
    return result, nodes


def _convert(binding: Binding, root: Path, source: Model) -> tuple[Model, dict[Path, bytes]]:
    try:
        result = binding.transform.convert(source.model_copy(deep=True))
        if type(result) is not binding.target.model:
            raise MigrationError("Transform must return its exact declared target model")
        result = binding.target.model.model_validate(input_values(binding.target.fields, result))
    except ValueError:
        raise MigrationError("Transform rejected a stored record") from None
    if binding.target.path(getattr(result, binding.target.key_field)) != root:
        raise MigrationError("Transform cannot change a record identity")
    return result, field_nodes(binding.target, root, result)


def _digest(root: Path, nodes: dict[Path, bytes]) -> str:
    """Version-one digest: length-delimited relative paths and exact encoded values."""
    digest = hashlib.sha256()
    for path, value in sorted(nodes.items(), key=lambda item: item[0].subscripts):
        parts = path.subscripts[len(root.subscripts) :]
        digest.update(len(parts).to_bytes(1))
        for part in parts:
            digest.update(len(part).to_bytes(4))
            digest.update(part)
        digest.update(len(value).to_bytes(4))
        digest.update(value)
    return digest.hexdigest()


def _digest_path(status: MigrationStatus, key: bytes) -> Path:
    return job_path(status.migration_id).child(b"records", hashlib.sha256(key).hexdigest().encode())


def _pin(
    store: Store,
    status: MigrationStatus,
    root: Path,
    before: dict[Path, bytes],
    after: dict[Path, bytes],
) -> None:
    key = root.subscripts[0]
    path = _digest_path(status, key)
    expected = canonical_json(
        {"key": key.hex(), "source": _digest(root, before), "target": _digest(root, after)}
    )
    current = store.get(path)
    if status.phase == "validate" and current is None:
        store.set(path, expected)
    elif current != expected:
        raise StorageError("Source data or transform output changed since validation")


def _verify_digest(
    store: Store, status: MigrationStatus, root: Path, nodes: dict[Path, bytes]
) -> None:
    key = root.subscripts[0]
    raw = store.get(_digest_path(status, key))
    try:
        document = json.loads(raw) if raw is not None else None
        if (
            not isinstance(document, dict)
            or canonical_json(document) != raw
            or set(document) != {"key", "source", "target"}
            or document["key"] != key.hex()
            or type(document["source"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", document["source"])
            or document["target"] != _digest(root, nodes)
        ):
            raise ValueError("Invalid transform digest")
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise StorageError("Transform record digest is missing or corrupt") from exc


def _claims(entries: frozenset[indexes.Entry]) -> frozenset[indexes.Entry]:
    return frozenset(entry for entry in entries if entry.claim_path is not None)


def _transform(store: Store, binding: Binding, status: MigrationStatus, root: Path) -> None:
    old, before = _read(store, binding.source, binding.source, root)
    new, after = _convert(binding, root, old)
    old_links = references.links(binding.source_registry, binding.source, root, old)
    new_links = references.links(binding.registry, binding.target, root, new)
    entries = indexes.entries(binding.target, root, new)
    validate_target_metadata(store, binding.registry, binding.source, binding.target, new_links)
    _pin(store, status, root, before, after)
    if status.phase == "validate":
        references.replace(store, old_links, old_links)
        previous = indexes.entries(binding.source, root, old)
        indexes.replace(store, previous, previous)
        migration_claims.reserve(store, status.migration_id, _claims(entries))
        return
    migration_claims.verify(store, status.migration_id, _claims(entries))
    references.replace(store, old_links, new_links)
    store.delete_tree(root)
    for key, value in metadata(binding.source).items():
        store.set(root.child(b"%m", key), value)
    for path, value in after.items():
        store.set(path, value)
    indexes.replace(store, frozenset(), entries)


def process(store: Store, binding: Binding, status: MigrationStatus, key: bytes) -> None:
    root = binding.source.path(decode_key(key))
    if status.phase in {"validate", "backfill"}:
        _transform(store, binding, status, root)
        return
    record, nodes = _read(store, binding.target, binding.source, root)
    _verify_digest(store, status, root, nodes)
    entries = indexes.entries(binding.target, root, record)
    links = references.links(binding.registry, binding.target, root, record)
    references.replace(store, links, links)
    validate_target_metadata(store, binding.registry, binding.source, binding.target, links)
    indexes.replace(store, entries, entries)
    migration_claims.verify(store, status.migration_id, _claims(entries))
    if status.phase == "retag":
        for name, value in metadata(binding.target).items():
            store.set(root.child(b"%m", name), value)
