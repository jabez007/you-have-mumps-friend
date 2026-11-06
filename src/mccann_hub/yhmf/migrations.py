# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic review plans. This module never executes migrations or writes data."""

import json
from dataclasses import dataclass, field
from typing import Any

from .codecs import LAYOUT_VERSION
from .schema_diff import SchemaChange, compare_schemas
from .schemas import SchemaInspection, SchemaSnapshot, canonical_json

_STEP_ORDER = {
    "validate_source_schema": 0,
    "manual_change": 10,
    "backfill_field": 20,
    "validate_records": 30,
    "validate_uniqueness": 40,
    "rebuild_references": 50,
    "drop_index": 60,
    "drop_uniqueness": 60,
    "rebuild_index": 70,
    "validate_indexes": 80,
    "update_record_metadata": 90,
    "activate_schema": 100,
    "install_schema": 100,
}


def _owned_paths(
    entries: list[dict[str, Any]], prefix: tuple[str, ...] = ()
) -> set[tuple[str, ...]]:
    result = set()
    for entry in entries:
        path = prefix + (entry["field"],)
        result.add(path)
        result.update(_owned_paths(entry.get("fields", []), path))
    return result


def _supported_change(change: SchemaChange, nonscalar: set[tuple[str, ...]]) -> bool:
    if change.destructive or change.requires_manual_review:
        return False
    if change.kind in {"metadata_changed", "schema_version_changed", "owned_version_changed"}:
        return True
    if (
        change.kind not in {"field_added", "index_added", "index_changed", "index_removed"}
        or not change.path
    ):
        return False
    if change.path in nonscalar:
        return False
    return change.kind != "field_added" or change.default_json is not None


@dataclass(frozen=True)
class MigrationStep:
    kind: str
    path: tuple[str, ...] = ()
    unique: bool = False
    default_json: bytes | None = field(default=None, repr=False)


def _snapshot_summary(snapshot: SchemaSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "global": snapshot.global_name,
        "fingerprint": snapshot.fingerprint,
        "layout": snapshot.layout_version,
        "version": snapshot.schema_version,
        "key": snapshot.primary_key,
    }


def _change_document(change: SchemaChange, include_defaults: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": change.kind,
        "path": list(change.path),
        "destructive": change.destructive,
        "requires_manual_review": change.requires_manual_review,
    }
    if change.kind.startswith("index_"):
        result["unique"] = change.unique
    if change.kind in {"field_added", "default_changed"}:
        result["default_available"] = change.default_json is not None
        if include_defaults and change.default_json is not None:
            result["default"] = json.loads(change.default_json)
    return result


def _step_document(step: MigrationStep, include_defaults: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": step.kind, "path": list(step.path)}
    if step.kind in {"rebuild_index", "drop_index", "drop_uniqueness", "validate_uniqueness"}:
        result["unique"] = step.unique
    if step.kind == "backfill_field":
        result["default_source"] = "declared"
        result["scope"] = "existing_owners"
        if include_defaults and step.default_json is not None:
            result["default"] = json.loads(step.default_json)
    return result


@dataclass(frozen=True)
class MigrationPlan:
    inspection: SchemaInspection = field(repr=False)
    changes: tuple[SchemaChange, ...]
    steps: tuple[MigrationStep, ...]

    @property
    def requires_manual_review(self) -> bool:
        return any(change.requires_manual_review for change in self.changes)

    @property
    def destructive(self) -> bool:
        return any(change.destructive for change in self.changes)

    @property
    def is_noop(self) -> bool:
        return not self.changes

    @property
    def execution_supported(self) -> bool:
        """Whether the plan fits scalar additions and index changes in existing owned trees."""
        source = self.inspection.installed
        if (
            source is None
            or source.layout_version != LAYOUT_VERSION
            or self.requires_manual_review
            or self.destructive
        ):
            return False
        document = self.inspection.declared.to_dict()
        nonscalar = _owned_paths(document.get("hierarchy", []))
        nonscalar.update(tuple(item["path"]) for item in document.get("references", []))
        return any(
            change.kind in {"field_added", "index_added", "index_changed", "index_removed"}
            for change in self.changes
        ) and all(_supported_change(change, nonscalar) for change in self.changes)

    def to_dict(self, *, include_defaults: bool = False) -> dict[str, Any]:
        """Return a review document; literal defaults require an explicit opt-in."""
        return {
            "format_version": 1,
            "execution_supported": self.execution_supported,
            "status": self.inspection.status,
            "has_data": self.inspection.has_data,
            "source": _snapshot_summary(self.inspection.installed),
            "target": _snapshot_summary(self.inspection.declared),
            "requires_manual_review": self.requires_manual_review,
            "destructive": self.destructive,
            "changes": [_change_document(change, include_defaults) for change in self.changes],
            "steps": [_step_document(step, include_defaults) for step in self.steps],
        }

    def to_json(self, *, include_defaults: bool = False) -> str:
        return canonical_json(self.to_dict(include_defaults=include_defaults)).decode("ascii")


def _field_steps(changes: tuple[SchemaChange, ...]) -> set[MigrationStep]:
    result = set()
    added_paths = {change.path for change in changes if change.kind == "field_added"}
    for change in changes:
        if change.requires_manual_review:
            result.add(MigrationStep("manual_change", change.path))
        if change.kind == "field_added" and not change.requires_manual_review:
            has_added_parent = any(
                change.path[:i] in added_paths for i in range(1, len(change.path))
            )
            if not has_added_parent:
                result.add(
                    MigrationStep("backfill_field", change.path, default_json=change.default_json)
                )
    return result


def _index_steps(changes: tuple[SchemaChange, ...]) -> set[MigrationStep]:
    result = set()
    for change in changes:
        if change.kind == "index_removed":
            result.add(MigrationStep("drop_index", change.path, unique=change.unique))
        elif change.kind == "index_changed" and not change.unique:
            result.add(MigrationStep("drop_uniqueness", change.path, unique=True))
        elif change.kind in {"index_added", "index_changed", "index_rebuild_required"}:
            result.add(MigrationStep("rebuild_index", change.path, unique=change.unique))
            if change.unique:
                result.add(MigrationStep("validate_uniqueness", change.path, unique=True))
        if change.kind in {"reference_added", "reference_removed", "reference_rebuild_required"}:
            result.add(MigrationStep("rebuild_references", change.path))
    if result:
        result.add(MigrationStep("validate_indexes"))
    return result


def build_plan(inspection: SchemaInspection) -> MigrationPlan:
    if inspection.status == "matching":
        return MigrationPlan(inspection, (), ())
    if inspection.installed is None:
        unmanaged = inspection.has_data
        change = SchemaChange(
            "unmanaged_data" if unmanaged else "schema_not_installed",
            requires_manual_review=unmanaged,
        )
        step = MigrationStep("manual_change" if unmanaged else "install_schema")
        return MigrationPlan(inspection, (change,), (step,))
    changes = compare_schemas(inspection.installed, inspection.declared)
    if not changes:
        return MigrationPlan(inspection, (), ())
    steps = _field_steps(changes) | _index_steps(changes)
    steps.update(
        MigrationStep(kind)
        for kind in (
            "validate_source_schema",
            "validate_records",
            "update_record_metadata",
            "activate_schema",
        )
    )
    ordered = tuple(sorted(steps, key=lambda step: (_STEP_ORDER[step.kind], step.path)))
    return MigrationPlan(inspection, changes, ordered)
