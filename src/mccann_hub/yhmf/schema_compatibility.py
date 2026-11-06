# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only legacy bindings. Cached work depends only on immutable input bytes."""

from dataclasses import replace
from functools import lru_cache
from typing import Any

from .errors import SchemaMismatch
from .registry import ModelSpec, _make_spec
from .schema_diff import compare_schemas
from .schemas import SchemaSnapshot, canonical_json

# This exact incidental pattern is qualified by both committed runtime fixtures.
# User patterns on strings, and other Decimal validation rules, are preserved.
_DECIMAL_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _normalize(item) for key, item in value.items()}
    variants = result.get("anyOf", [])
    if isinstance(variants, list) and len(variants) == 2:
        if {item.get("type") for item in variants if isinstance(item, dict)} == {
            "number",
            "string",
        }:
            for item in variants:
                if item.get("type") == "string" and item.get("pattern") == _DECIMAL_PATTERN:
                    del item["pattern"]
    return result


@lru_cache(maxsize=128)
def _legacy_bytes(spec: ModelSpec) -> bytes:
    return _make_spec(spec.model, legacy=True).snapshot


@lru_cache(maxsize=128)
def _compatible(legacy: bytes, installed: bytes) -> bool:
    old = SchemaSnapshot(canonical_json(_normalize(SchemaSnapshot(installed).to_dict())))
    new = SchemaSnapshot(canonical_json(_normalize(SchemaSnapshot(legacy).to_dict())))
    return all(change.kind == "metadata_changed" for change in compare_schemas(old, new))


def bind_snapshot(spec: ModelSpec, snapshot: SchemaSnapshot) -> ModelSpec:
    """Use the installed identity only after integrity and semantic verification."""
    if snapshot.canonical == spec.snapshot:
        return spec
    if snapshot.format_version == 1 and _compatible(_legacy_bytes(spec), snapshot.canonical):
        return replace(
            spec, snapshot=snapshot.canonical, fingerprint=snapshot.fingerprint.encode("ascii")
        )
    raise SchemaMismatch("Installed schema differs; an explicit migration is required")
