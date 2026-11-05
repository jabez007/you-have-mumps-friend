# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated model declarations and canonical installed-schema identities."""

import hashlib
import json
import re
import types
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Union, get_args, get_origin

from .codecs import LAYOUT_VERSION
from .errors import SchemaError
from .models import Children, Model
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
class FieldSpec:
    name: str
    kind: Literal["scalar", "embedded", "children"]
    node: "NodeSpec | None" = None


@dataclass(frozen=True)
class NodeSpec:
    model: type[Model]
    key_field: str | None
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class ModelSpec:
    model: type[Model]
    global_name: str
    key_field: str
    fields: tuple[FieldSpec, ...]
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


def _field_spec(model: type[Model], name: str, ancestors: tuple[type[Model], ...]) -> FieldSpec:
    field = model.model_fields[name]
    annotation = scalar_type(field.annotation)
    extra = field.json_schema_extra
    metadata = extra.get("yhmf", {}) if isinstance(extra, dict) else {}
    if not isinstance(metadata, dict):
        raise SchemaError("Invalid mapper metadata")
    if get_origin(annotation) is Children:
        child_model = get_args(annotation)[0]
        node = _node_spec(child_model, keyed=True, ancestors=ancestors)
        if metadata.get("children_key") != node.key_field:
            raise SchemaError("ChildField key must match the child's primary-key field")
        return FieldSpec(name, "children", node)
    if "children_key" in metadata:
        raise SchemaError("ChildField requires a Children[Model] annotation")
    if isinstance(annotation, type) and issubclass(annotation, Model):
        return FieldSpec(name, "embedded", _node_spec(annotation, keyed=False, ancestors=ancestors))
    if annotation not in _SCALARS:
        raise SchemaError("Unsupported field type; use scalar, Model, or Children[Model]")
    return FieldSpec(name, "scalar")


def _node_spec(
    model: type[Model],
    *,
    keyed: bool,
    ancestors: tuple[type[Model], ...],
) -> NodeSpec:
    if model in ancestors:
        raise SchemaError("Recursive ownership is not supported")
    model.model_rebuild()
    if ancestors and model.__global__ is not None:
        raise SchemaError("Owned models cannot declare an independent global")
    if type(model.__schema_version__) is not int or model.__schema_version__ < 1:
        raise SchemaError("Schema versions must be positive integers")
    if model.model_computed_fields:
        raise SchemaError("Computed fields are not persisted")
    for name, field in model.model_fields.items():
        if not _NAME.fullmatch(name) or any(
            (field.alias, field.validation_alias, field.serialization_alias)
        ):
            raise SchemaError("Fields must have plain names without aliases")
    key = _key_field(model) if keyed else None
    fields = tuple(_field_spec(model, name, ancestors + (model,)) for name in model.model_fields)
    return NodeSpec(model, key, fields)


def _hierarchy(fields: tuple[FieldSpec, ...]) -> list[dict[str, Any]]:
    return [
        {
            "field": field.name,
            "kind": field.kind,
            "key": field.node.key_field,
            "version": field.node.model.__schema_version__,
            "fields": _hierarchy(field.node.fields),
        }
        for field in fields
        if field.node is not None
    ]


def _make_spec(model: type[Model]) -> ModelSpec:
    model.model_rebuild()
    global_name = model.__global__
    if global_name is None or global_name in {"^YHMFSchema", "^YHMFIndex"}:
        raise SchemaError("Declare an application-owned __global__")
    Path(global_name)
    node = _node_spec(model, keyed=True, ancestors=())
    key = _key_field(model)
    declaration = {
        "layout": LAYOUT_VERSION,
        "global": global_name,
        "version": model.__schema_version__,
        "key": key,
        "declaration": model.model_json_schema(),
    }
    hierarchy = _hierarchy(node.fields)
    if hierarchy:
        declaration["hierarchy"] = hierarchy
    snapshot = json.dumps(
        declaration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return ModelSpec(
        model,
        global_name,
        key,
        node.fields,
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
