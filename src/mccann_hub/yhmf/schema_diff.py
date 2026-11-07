# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative comparisons of supported mapper snapshots, without live models."""

import json
import re
from dataclasses import dataclass, field
from decimal import InvalidOperation
from typing import Any, cast

from .canonical_schema import numeric_rule
from .errors import StorageError
from .schemas import SchemaSnapshot, canonical_json
from .storage import Path

_PRESENTATION = {"title", "description", "examples", "deprecated", "readOnly", "writeOnly"}
_IGNORED_RULES = _PRESENTATION | {
    "default",
    "yhmf",
    "$defs",
    "type",
    "format",
    "anyOf",
    "required",
    "codec",
    "default_factory",
    "custom_validation",
}
_SNAPSHOT_KEYS = {
    "declaration",
    "global",
    "key",
    "layout",
    "version",
    "hierarchy",
    "references",
    "format_version",
    "codec_version",
}


@dataclass(frozen=True)
class SchemaChange:
    kind: str
    path: tuple[str, ...] = ()
    destructive: bool = False
    requires_manual_review: bool = False
    unique: bool = False
    default_json: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _Field:
    kind: str
    type_signature: bytes
    validation: tuple[bytes, ...]
    relationship: bytes
    nullable: bool
    required: bool
    default_json: bytes | None
    index: bool
    unique: bool
    primary: bool
    version: int | None


class _Unsupported(Exception):
    def __init__(self, path: tuple[str, ...] = ()) -> None:
        self.path = path


def _rules(schema: dict[str, Any]) -> bytes:
    rules = {
        key: value
        for key, value in schema.items()
        if key not in _IGNORED_RULES | {"properties", "$ref"}
    }
    if isinstance(rules.get("additionalProperties"), dict):
        del rules["additionalProperties"]
    return canonical_json(rules)


def _field_rules(raw: dict[str, Any], variants: list[Any], *, legacy: bool) -> tuple[bytes, ...]:
    rules = {}
    for part in (raw, *variants):
        rules.update(json.loads(_rules(part)))
    if legacy and {part.get("type") for part in variants} == {"number", "string"}:
        if rules.get("pattern") == r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$":
            del rules["pattern"]
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if name in rules:
            try:
                rules[name] = numeric_rule(rules[name])
            except (InvalidOperation, ValueError):
                raise _Unsupported() from None
    return (canonical_json(rules),)


class _Fields:
    def __init__(self, snapshot: SchemaSnapshot, *, legacy_comparison: bool = False) -> None:
        self.legacy_comparison = legacy_comparison
        document = snapshot.to_dict()
        if document.keys() - _SNAPSHOT_KEYS:
            raise _Unsupported()
        self.declaration = document["declaration"]
        self.definitions = self.declaration.get("$defs", {})
        if not isinstance(self.definitions, dict):
            raise _Unsupported()
        self.owned: dict[tuple[str, ...], dict[str, Any]] = {}
        self.references: dict[tuple[str, ...], dict[str, Any]] = {}
        self._ownership(document.get("hierarchy", []), ())
        self._references(document.get("references", []))
        self.fields: dict[tuple[str, ...], _Field] = {}
        self._walk(self.declaration, ())
        if (self.owned.keys() | self.references.keys()) - self.fields.keys():
            raise _Unsupported()

    def _ownership(self, entries: Any, prefix: tuple[str, ...]) -> None:
        if not isinstance(entries, list) or len(prefix) > 31:
            raise _Unsupported(prefix)
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("field"), str):
                raise _Unsupported(prefix)
            path = prefix + (entry["field"],)
            kind = entry.get("kind")
            if (
                path in self.owned
                or not isinstance(kind, str)
                or kind not in {"embedded", "children"}
            ):
                raise _Unsupported(path)
            if entry.keys() - {"field", "kind", "key", "version", "fields"}:
                raise _Unsupported(path)
            if type(entry.get("version")) is not int or entry["version"] < 1:
                raise _Unsupported(path)
            if kind == "children" and not isinstance(entry.get("key"), str):
                raise _Unsupported(path)
            self.owned[path] = entry
            self._ownership(entry.get("fields", []), path)

    def _references(self, entries: Any) -> None:
        if not isinstance(entries, list):
            raise _Unsupported()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), list):
                raise _Unsupported()
            if not entry["path"] or any(not isinstance(part, str) for part in entry["path"]):
                raise _Unsupported()
            path = tuple(entry["path"])
            if path in self.references or path in self.owned:
                raise _Unsupported(path)
            if not all(isinstance(entry.get(key), str) for key in ("global", "key", "on_delete")):
                raise _Unsupported(path)
            if (
                entry.keys() - {"path", "global", "key", "on_delete", "key_codec"}
                or entry["on_delete"] != "restrict"
            ):
                raise _Unsupported(path)
            try:
                Path(entry["global"])
            except StorageError as exc:
                raise _Unsupported(path) from exc
            self.references[path] = entry

    def _resolve(self, schema: Any, path: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(schema, dict):
            raise _Unsupported(path)
        seen = set()
        while "$ref" in schema:
            ref = schema["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/") or ref in seen:
                raise _Unsupported(path)
            seen.add(ref)
            name = ref[len("#/$defs/") :].replace("~1", "/").replace("~0", "~")
            target = self.definitions.get(name)
            if not isinstance(target, dict):
                raise _Unsupported(path)
            schema = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
        if schema.keys() & {"oneOf", "allOf", "not", "if", "then", "else"}:
            raise _Unsupported(path)
        return cast(dict[str, Any], schema)

    def _variants(self, raw: dict[str, Any], path: tuple[str, ...]) -> tuple[list[Any], bool]:
        variants = raw.get("anyOf", [raw])
        if not isinstance(variants, list) or not variants:
            raise _Unsupported(path)
        resolved = [self._resolve(item, path) for item in variants]
        nonnull = [item for item in resolved if item.get("type") != "null"]
        if not nonnull:
            raise _Unsupported(path)
        return nonnull, len(nonnull) != len(resolved)

    def _walk(self, schema: Any, prefix: tuple[str, ...]) -> None:
        schema = self._resolve(schema, prefix)
        properties, required = schema.get("properties"), schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) or len(prefix) > 31:
            raise _Unsupported(prefix)
        if any(not isinstance(name, str) or name not in properties for name in required):
            raise _Unsupported(prefix)
        for name, property_schema in properties.items():
            path = prefix + (name,)
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise _Unsupported(path)
            raw = self._resolve(property_schema, path)
            variants, nullable = self._variants(raw, path)
            state = self._field(path, raw, variants, nullable, name in required)
            self.fields[path] = state
            if state.kind in {"embedded", "children"}:
                if len(variants) != 1:
                    raise _Unsupported(path)
                child = variants[0]
                if state.kind == "children":
                    child = child.get("additionalProperties")
                self._walk(child, path)

    def _field(
        self,
        path: tuple[str, ...],
        raw: dict[str, Any],
        variants: list[Any],
        nullable: bool,
        required: bool,
    ) -> _Field:
        metadata = raw.get("yhmf", {})
        if not isinstance(metadata, dict) or metadata.keys() - {
            "primary_key",
            "index",
            "unique",
            "children_key",
            "on_delete",
        }:
            raise _Unsupported(path)
        if any(
            type(metadata.get(flag, False)) is not bool
            for flag in ("primary_key", "index", "unique")
        ):
            raise _Unsupported(path)
        relationship = self.references.get(path) or self.owned.get(path)
        kind = (
            "reference"
            if path in self.references
            else self.owned.get(path, {}).get("kind", "scalar")
        )
        if kind != "scalar" and (len(variants) != 1 or variants[0].get("type") != "object"):
            raise _Unsupported(path)
        signature = self._types(kind, variants, path)
        rules = _field_rules(raw, variants, legacy=self.legacy_comparison)
        relation = {
            key: value
            for key, value in (relationship or {}).items()
            if key not in {"path", "field", "fields", "version"}
        }
        if self.legacy_comparison:
            relation.pop("key_codec", None)
        return _Field(
            kind,
            signature,
            rules,
            canonical_json(relation),
            nullable,
            required,
            canonical_json(raw["default"]) if "default" in raw else None,
            bool(metadata.get("index") or metadata.get("unique")),
            bool(metadata.get("unique")),
            bool(metadata.get("primary_key")),
            self.owned.get(path, {}).get("version"),
        )

    def _types(self, kind: str, variants: list[Any], path: tuple[str, ...]) -> bytes:
        if kind != "scalar":
            return canonical_json(kind)
        types = [_scalar_type(variant, path, legacy=self.legacy_comparison) for variant in variants]
        if len(types) > 1 and set(types) != {("number", None), ("string", None)}:
            raise _Unsupported(path)
        return canonical_json(sorted(types, key=canonical_json))


def _scalar_type(
    variant: dict[str, Any], path: tuple[str, ...], *, legacy: bool
) -> tuple[str, str | None] | tuple[str, str, str]:
    name = variant.get("type")
    if not isinstance(name, str) or name not in {"string", "integer", "number", "boolean"}:
        raise _Unsupported(path)
    format_name = variant.get("format")
    if format_name is not None and (
        format_name not in {"binary", "date", "date-time", "uuid"} or name != "string"
    ):
        raise _Unsupported(path)
    if format_name in {"date", "date-time", "uuid"}:
        codec = {"date": "date-v1", "date-time": "datetime-utc-v1", "uuid": "uuid-v1"}[format_name]
        if variant.get("codec", codec if legacy else None) != codec:
            raise _Unsupported(path)
        return name, format_name, codec
    return name, format_name


def _added(path: tuple[str, ...], value: _Field) -> list[SchemaChange]:
    known_null_reference = (
        value.kind == "reference" and value.nullable and value.default_json == b"null"
    )
    manual = value.default_json is None or (value.kind != "scalar" and not known_null_reference)
    changes = [
        SchemaChange(
            "field_added", path, requires_manual_review=manual, default_json=value.default_json
        )
    ]
    if value.index:
        changes.append(SchemaChange("index_added", path, unique=value.unique))
    if value.kind == "reference":
        changes.append(SchemaChange("reference_added", path))
    return changes


def _removed(path: tuple[str, ...], value: _Field) -> list[SchemaChange]:
    changes = [SchemaChange("field_removed", path, destructive=True, requires_manual_review=True)]
    if value.index:
        changes.append(SchemaChange("index_removed", path, unique=value.unique))
    if value.kind == "reference":
        changes.append(SchemaChange("reference_removed", path))
    return changes


def _changed(path: tuple[str, ...], old: _Field, new: _Field) -> list[SchemaChange]:
    changes = []
    if old.kind != new.kind or old.relationship != new.relationship:
        changes.append(
            SchemaChange(
                "relationship_changed", path, destructive=True, requires_manual_review=True
            )
        )
    elif old.type_signature != new.type_signature:
        changes.append(
            SchemaChange("type_changed", path, destructive=True, requires_manual_review=True)
        )
        if old.primary or new.primary:
            changes.append(
                SchemaChange(
                    "identity_changed", path[:-1], destructive=True, requires_manual_review=True
                )
            )
    if old.nullable != new.nullable:
        changes.append(SchemaChange("nullability_changed", path))
    if old.required != new.required or old.validation != new.validation:
        changes.append(SchemaChange("validation_changed", path))
    if old.default_json != new.default_json:
        changes.append(SchemaChange("default_changed", path, default_json=new.default_json))
    if old.version != new.version:
        downgrade = (
            old.version is not None and new.version is not None and new.version < old.version
        )
        changes.append(
            SchemaChange("owned_version_changed", path, requires_manual_review=downgrade)
        )
    if old.index != new.index:
        changes.append(
            SchemaChange(
                "index_added" if new.index else "index_removed",
                path,
                unique=new.unique if new.index else old.unique,
            )
        )
    elif new.index and old.unique != new.unique:
        changes.append(SchemaChange("index_changed", path, unique=new.unique))
    return changes


def _headers(old: SchemaSnapshot, new: SchemaSnapshot) -> list[SchemaChange]:
    changes = []
    if old.global_name != new.global_name:
        changes.append(
            SchemaChange("global_changed", destructive=True, requires_manual_review=True)
        )
    if old.primary_key != new.primary_key:
        changes.append(
            SchemaChange("primary_key_changed", destructive=True, requires_manual_review=True)
        )
    if old.layout_version != new.layout_version or new.layout_version != 1:
        changes.append(SchemaChange("layout_changed", requires_manual_review=True))
    if old.to_dict().get("codec_version", 1) != new.to_dict().get("codec_version", 1):
        changes.append(SchemaChange("codec_changed", requires_manual_review=True))
    if old.schema_version != new.schema_version:
        downgrade = new.schema_version < old.schema_version
        changes.append(SchemaChange("schema_version_changed", requires_manual_review=downgrade))
    return changes


def _dependent_rebuilds(
    changes: list[SchemaChange],
    old: _Fields,
    new: _Fields,
) -> list[SchemaChange]:
    changed_paths = {
        change.path
        for change in changes
        if change.kind
        in {
            "type_changed",
            "relationship_changed",
            "identity_changed",
            "global_changed",
            "primary_key_changed",
            "layout_changed",
        }
    }
    result = []
    for path, value in new.fields.items():
        affected = any(path[: len(prefix)] == prefix for prefix in changed_paths)
        if not affected or path not in old.fields:
            continue
        if value.index:
            result.append(SchemaChange("index_rebuild_required", path, unique=value.unique))
        if value.kind == "reference":
            result.append(SchemaChange("reference_rebuild_required", path))
    return result


def compare_schemas(old: SchemaSnapshot, new: SchemaSnapshot) -> tuple[SchemaChange, ...]:
    """Compare physical fields conservatively. Unknown formats require manual review."""
    if old.fingerprint == new.fingerprint:
        return ()
    changes = _headers(old, new)
    try:
        legacy = old.format_version != new.format_version
        before, after = (
            _Fields(old, legacy_comparison=legacy),
            _Fields(new, legacy_comparison=legacy),
        )
        for path in sorted(before.fields.keys() | after.fields.keys()):
            if path not in before.fields:
                changes.extend(_added(path, after.fields[path]))
            elif path not in after.fields:
                changes.extend(_removed(path, before.fields[path]))
            else:
                changes.extend(_changed(path, before.fields[path], after.fields[path]))
        if _rules(before.declaration) != _rules(after.declaration):
            changes.append(SchemaChange("validation_changed"))
        changes.extend(_dependent_rebuilds(changes, before, after))
    except _Unsupported as exc:
        changes.append(SchemaChange("unsupported_schema", exc.path, requires_manual_review=True))
    if not changes:
        changes.append(SchemaChange("metadata_changed"))
    return tuple(sorted(set(changes), key=lambda change: (change.path, change.kind)))
