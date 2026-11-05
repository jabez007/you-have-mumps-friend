# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile declared migration literals and apply them to copied source values."""

from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .errors import MigrationError
from .migrations import MigrationPlan
from .registry import ModelSpec


class BackfillDefaults:
    """Prepare target input without running default factories or mutating source input."""

    def __init__(self, spec: ModelSpec, plan: MigrationPlan) -> None:
        self._values = _literal_defaults(spec, plan)

    def apply(self, values: dict[str, Any]) -> dict[str, Any]:
        return {**deepcopy(values), **deepcopy(self._values)}


def _literal_defaults(spec: ModelSpec, plan: MigrationPlan) -> dict[str, Any]:
    result = {}
    for change in plan.changes:
        if change.kind != "field_added":
            continue
        name = change.path[0]
        field = spec.model.model_fields[name]
        if field.default_factory is not None:
            raise MigrationError("Migration defaults must be literals")
        assert change.default_json is not None
        try:
            result[name] = TypeAdapter(field.rebuild_annotation()).validate_json(
                change.default_json, strict=True
            )
        except ValidationError:
            raise MigrationError("A literal default does not satisfy its target field") from None
    return result
