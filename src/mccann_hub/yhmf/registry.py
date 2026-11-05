# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated model declarations and canonical installed-schema identities."""

import hashlib
import json
import re
import types
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Union, get_args, get_origin

from .codecs import LAYOUT_VERSION
from .errors import SchemaError
from .models import Model
from .storage import Path

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_SCALARS = {str, int, bool, float, Decimal, bytes}


def scalar_type(annotation: Any) -> Any:
    """Unwrap nullable scalar types without coercing ambiguous unions."""
    if get_origin(annotation) in (Union, types.UnionType):
        options = [t for t in get_args(annotation) if t is not type(None)]
        if len(options) == 1:
            return options[0]
    return annotation


@dataclass(frozen=True)
class ModelSpec:
    model: type[Model]
    global_name: str
    key_field: str
    fields: tuple[str, ...]
    snapshot: bytes
    fingerprint: bytes

    def path(self, key: Any) -> Path:
        from .codecs import encode_key

        annotation = self.model.model_fields[self.key_field].annotation
        if type(key) is not annotation:
            raise SchemaError("Key type does not match the model declaration")
        return Path(self.global_name).child(encode_key(key))


def _key_field(model: type[Model]) -> str:
    keys = []
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        metadata = extra.get("yhmf") if isinstance(extra, dict) else None
        if isinstance(metadata, dict):
            if metadata.get("primary_key"):
                keys.append(name)
    if len(keys) != 1:
        raise SchemaError("Each persisted model must declare exactly one primary key")
    key = keys[0]
    if model.model_fields[key].annotation not in (str, int):
        raise SchemaError("Primary keys must be nonnullable str or int fields")
    return key


def _make_spec(model: type[Model]) -> ModelSpec:
    model.model_rebuild()
    global_name = model.__global__
    if global_name is None or global_name.startswith("^%YHMF"):
        raise SchemaError("Declare an application-owned __global__")
    Path(global_name)
    if type(model.__schema_version__) is not int or model.__schema_version__ < 1:
        raise SchemaError("Schema versions must be positive integers")
    if model.model_computed_fields:
        raise SchemaError("Computed fields are not persisted")
    for name, field in model.model_fields.items():
        if (
            not _NAME.fullmatch(name)
            or field.alias
            or field.validation_alias
            or field.serialization_alias
        ):
            raise SchemaError("Fields must have plain names without aliases")
        if scalar_type(field.annotation) not in _SCALARS:
            raise SchemaError("Only scalar fields are supported in this slice")
    key = _key_field(model)
    snapshot = json.dumps(
        {
            "layout": LAYOUT_VERSION,
            "global": global_name,
            "version": model.__schema_version__,
            "key": key,
            "declaration": model.model_json_schema(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return ModelSpec(
        model,
        global_name,
        key,
        tuple(model.model_fields),
        snapshot,
        hashlib.sha256(snapshot).hexdigest().encode("ascii"),
    )


class Registry:
    """Explicitly register top-level models and reject overlapping global ownership."""

    def __init__(self, *models: type[Model]) -> None:
        self._specs: dict[type[Model], ModelSpec] = {}
        self._globals: dict[str, type[Model]] = {}
        for model in models:
            self.register(model)

    def register(self, model: type[Model]) -> ModelSpec:
        if model in self._specs:
            return self._specs[model]
        spec = _make_spec(model)
        if spec.global_name in self._globals:
            raise SchemaError("A global can belong to only one registered model")
        self._globals[spec.global_name] = model
        self._specs[model] = spec
        return spec

    def spec(self, model: type[Model]) -> ModelSpec:
        try:
            return self._specs[model]
        except KeyError as exc:
            raise SchemaError("Register the model before database access") from exc
