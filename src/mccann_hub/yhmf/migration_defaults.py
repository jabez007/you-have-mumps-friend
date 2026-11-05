# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile declared migration literals and apply them to copied source values."""

from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from .errors import MigrationError, StorageError
from .migrations import MigrationPlan
from .records import MAX_RECORD_NODES
from .registry import FieldSpec, ModelSpec


class BackfillDefaults:
    """Prepare target input without running default factories or mutating source input."""

    def __init__(self, spec: ModelSpec, plan: MigrationPlan) -> None:
        self._fields = spec.fields
        self._values = _literal_defaults(spec, plan)

    def apply(self, values: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(values)
        self._apply_fields(self._fields, result, (), MAX_RECORD_NODES)
        return result

    def _apply_fields(
        self,
        fields: tuple[FieldSpec, ...],
        values: dict[str, Any],
        prefix: tuple[str, ...],
        remaining: int,
    ) -> int:
        for field in fields:
            path = prefix + (field.name,)
            if path in self._values:
                if remaining == 0:
                    raise StorageError("Backfill exceeds the record node budget")
                remaining -= 1
                values[field.name] = deepcopy(self._values[path])
            elif field.node is not None:
                value = values[field.name]
                if value is not None:
                    owners = value.values() if field.kind == "children" else (value,)
                    for owner in owners:
                        remaining = self._apply_fields(field.node.fields, owner, path, remaining)
        return remaining


def _field_info(spec: ModelSpec, path: tuple[str, ...]) -> FieldInfo:
    owner, fields = spec.model, spec.fields
    for name in path[:-1]:
        field = next((field for field in fields if field.name == name), None)
        if field is None or field.node is None:
            raise MigrationError("Backfill owners must be embedded objects or child collections")
        owner, fields = field.node.model, field.node.fields
    leaf = next((field for field in fields if field.name == path[-1]), None)
    if leaf is None or leaf.kind != "scalar":
        raise MigrationError("Backfills require declared scalar fields")
    return owner.model_fields[leaf.name]


def _literal_defaults(spec: ModelSpec, plan: MigrationPlan) -> dict[tuple[str, ...], Any]:
    result = {}
    for change in plan.changes:
        if change.kind != "field_added":
            continue
        field = _field_info(spec, change.path)
        if field.default_factory is not None:
            raise MigrationError("Migration defaults must be literals")
        assert change.default_json is not None
        try:
            result[change.path] = TypeAdapter(field.rebuild_annotation()).validate_json(
                change.default_json, strict=True
            )
        except ValidationError:
            raise MigrationError("A literal default does not satisfy its target field") from None
    return result
