# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned persistence declarations, independent of Pydantic JSON Schema output.

The declaration grammar is intentionally shared with the migration inspector.
Callable behavior is an application schema revision, never a Python code hash.
"""

import math
import re
import types
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import (
    AfterValidator,
    BeforeValidator,
    PlainSerializer,
    PlainValidator,
    WithJsonSchema,
    WrapSerializer,
    WrapValidator,
)
from pydantic.fields import FieldInfo

from . import temporal
from .codecs import LAYOUT_VERSION, canonical_json
from .errors import SchemaError
from .models import Model, Reference

if TYPE_CHECKING:
    from .registry import FieldSpec, NodeSpec

FORMAT_VERSION = 2
CODEC_VERSION = 1
_CALLABLES = (AfterValidator, BeforeValidator, PlainValidator, WrapValidator)
_RULES = {
    "gt": "exclusiveMinimum",
    "ge": "minimum",
    "lt": "exclusiveMaximum",
    "le": "maximum",
    "multiple_of": "multipleOf",
    "min_length": "minLength",
    "max_length": "maxLength",
    "pattern": "pattern",
    "max_digits": "max_digits",
    "decimal_places": "decimal_places",
    "strict": "strict",
    "allow_inf_nan": "allow_inf_nan",
    "strip_whitespace": "strip_whitespace",
    "to_lower": "to_lower",
    "to_upper": "to_upper",
    "ascii_only": "ascii_only",
}
_POLICY_DEFAULTS = {
    "strict": True,
    "str_min_length": 0,
    "str_max_length": None,
    "str_strip_whitespace": False,
    "str_to_lower": False,
    "str_to_upper": False,
    "coerce_numbers_to_str": False,
    "regex_engine": "rust-regex",
}


def numeric_rule(value: Any) -> str:
    """Canonical exact coefficient/exponent, with no decimal-context rounding."""
    number = Decimal(str(value))
    if not number.is_finite():
        raise SchemaError("Schema numeric constraints must be finite")
    sign, digits, exponent = number.as_tuple()
    coefficient = "".join(map(str, digits)).rstrip("0")
    if not coefficient:
        return "0"
    power = cast(int, exponent) + len(digits) - len(coefficient)
    return f"{'-' if sign else ''}{coefficient}E{power}"


def _value(value: Any) -> Any:
    if (
        value is None
        or type(value) in (str, bool, int)
        or (type(value) is float and math.isfinite(value))
    ):
        return value
    if type(value) in (date, datetime):
        return temporal.literal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SchemaError("Schema defaults and constraints must be finite")
        return str(value)
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if isinstance(value, re.Pattern):
        return {"source": value.pattern, "flags": value.flags}
    if isinstance(value, (Model, Reference)):
        return {name: _value(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return _mapping(value)
    raise SchemaError(
        "Schema defaults and constraints require supported deterministic values "
        "in finite canonical JSON"
    )


def _mapping(value: dict[Any, Any]) -> dict[str, Any]:
    if all(type(key) is str for key in value):
        return {key: _value(item) for key, item in value.items()}
    if all(type(key) in (str, int) for key in value):
        return {"$map": [[key, _value(value[key])] for key in sorted(value, key=canonical_json)]}
    raise SchemaError("Schema default mappings require string or integer keys")


def _metadata(item: Any) -> dict[str, Any]:
    if isinstance(item, (PlainSerializer, WrapSerializer, WithJsonSchema)):
        return {}  # Persistence uses its own codecs and never these presentation hooks.
    if isinstance(item, _CALLABLES):
        return {"custom_validation": True}
    attributes = (
        vars(item)
        if hasattr(item, "__dict__")
        else {key: getattr(item, key) for key in getattr(item, "__slots__", ())}
    )
    # Type annotations such as Strict() and annotated-types constraints expose
    # values through these public metadata attributes, not generated JSON.
    unknown = attributes.keys() - _RULES.keys()
    if unknown:
        raise SchemaError("Unsupported validation metadata in canonical schema")
    return {_RULES[key]: _value(value) for key, value in attributes.items() if value is not None}


def _rules(field: FieldInfo, *, temporal: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in field.metadata:
        result.update(_metadata(item))
    if temporal:
        if "multipleOf" in result:
            raise SchemaError("Temporal fields do not support multiple_of")
        for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if name in result:
                result["temporal_" + name] = result.pop(name)
        return result
    for name in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if name in result:
            result[name] = numeric_rule(result[name])
    return result


def _scalar(annotation: Any) -> dict[str, Any]:
    mapping = {str: "string", int: "integer", bool: "boolean", float: "number"}
    if annotation in (date, datetime):
        return {
            "codec": "date-v1" if annotation is date else "datetime-utc-v1",
            "type": "string",
            "format": "date" if annotation is date else "date-time",
        }
    if annotation is Decimal:
        return {"codec": "decimal", "anyOf": [{"type": "number"}, {"type": "string"}]}
    if annotation is bytes:
        return {"codec": "bytes", "type": "string", "format": "binary"}
    return {"codec": annotation.__name__, "type": mapping[annotation]}


def _field(field: "FieldSpec", declaration: FieldInfo, *, primary: bool) -> dict[str, Any]:
    annotation = declaration.annotation
    nullable = get_origin(annotation) in (Union, types.UnionType) and type(None) in get_args(
        annotation
    )
    if nullable:
        annotation = next(item for item in get_args(annotation) if item is not type(None))
    if field.node is not None:
        result = _node(field.node)
        if field.kind == "children":
            result = {"type": "object", "additionalProperties": result}
    elif field.target is not None:
        result = {"type": "object"}
    else:
        result = _scalar(annotation)
    if nullable:
        result = {"anyOf": [result, {"type": "null"}]}
    result.update(_rules(declaration, temporal=annotation in (date, datetime)))
    if metadata := _mapper_metadata(field, primary):
        result["yhmf"] = metadata
    if declaration.default_factory is not None:
        result["default_factory"] = True
    elif not declaration.is_required():
        result["default"] = _value(declaration.default)
    return result


def _mapper_metadata(field: "FieldSpec", primary: bool) -> dict[str, Any]:
    # Compile actual registry semantics; spelling an implicit policy explicitly,
    # or adding Field() solely for presentation, must not change identity.
    result: dict[str, Any] = {
        name: True
        for name, present in (
            ("primary_key", primary),
            ("index", field.index),
            ("unique", field.unique),
        )
        if present
    }
    if field.kind == "children":
        assert field.node is not None
        result["children_key"] = field.node.key_field
    return result


def _node(node: "NodeSpec") -> dict[str, Any]:
    model = node.model
    result: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field.name: _field(
                field, model.model_fields[field.name], primary=field.name == node.key_field
            )
            for field in sorted(node.fields, key=lambda f: f.name)
        },
        "required": sorted(
            name for name, field in model.model_fields.items() if field.is_required()
        ),
    }
    policy = {
        name: model.model_config.get(name, default)
        for name, default in _POLICY_DEFAULTS.items()
        if model.model_config.get(name, default) != default
    }
    if policy:
        result["validation_policy"] = policy
    decorators = model.__pydantic_decorators__
    if (
        decorators.field_validators
        or decorators.model_validators
        or decorators.validators
        or decorators.root_validators
    ):
        result["custom_validation"] = True
    return result


def _ownership(fields: tuple["FieldSpec", ...]) -> list[dict[str, Any]]:
    return [
        {
            "field": field.name,
            "kind": field.kind,
            "key": field.node.key_field,
            "version": field.node.model.__schema_version__,
            "fields": _ownership(field.node.fields),
        }
        for field in sorted(fields, key=lambda f: f.name)
        if field.node is not None
    ]


def _references(
    fields: tuple["FieldSpec", ...], prefix: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    result = []
    for field in sorted(fields, key=lambda f: f.name):
        path = prefix + (field.name,)
        if field.target is not None:
            key_type = field.target.model_fields[field.target_key or ""].annotation
            assert key_type in (str, int)  # Registry has validated the target key.
            result.append(
                {
                    "path": path,
                    "global": field.target.__global__,
                    "key": field.target_key,
                    "key_codec": key_type.__name__,
                    "on_delete": "restrict",
                }
            )
        elif field.node is not None:
            result.extend(_references(field.node.fields, path))
    return result


def schema_document(node: "NodeSpec", global_name: str) -> dict[str, Any]:
    """Build format two without invoking serializers, defaults, or JSON Schema hooks."""
    result: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "codec_version": CODEC_VERSION,
        "layout": LAYOUT_VERSION,
        "global": global_name,
        "version": node.model.__schema_version__,
        "key": node.key_field,
        "declaration": _node(node),
    }
    if hierarchy := _ownership(node.fields):
        result["hierarchy"] = hierarchy
    if references := _references(node.fields):
        result["references"] = references
    return result


def schema_bytes(node: "NodeSpec", global_name: str) -> bytes:
    return canonical_json(schema_document(node, global_name))
