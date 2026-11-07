# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile strict M field constants independently of their ownership address."""

import hashlib
from decimal import Decimal
from typing import Any

from .codecs import canonical_json, encode_scalar
from .decimal_math import units
from .errors import SchemaError
from .models import Model, Reference
from .ordered import value_key
from .records import metadata
from .registry import FieldSpec, ModelSpec, NodeSpec, scalar_type, supports_local_validation

_BOUNDS = {"minimum": "ge", "maximum": "le", "exclusiveMinimum": "gt", "exclusiveMaximum": "lt"}
_RULES = {
    "codec",
    "type",
    "default",
    "default_factory",
    "yhmf",
    "strict",
    "minLength",
    "maxLength",
    *_BOUNDS,
}


def _literal(value: str) -> str:
    if not value.isascii() or any(ord(char) < 32 for char in value):
        raise SchemaError("Generated M configuration requires printable ASCII")
    return '"' + value.replace('"', '""') + '"'


def _set(*parts: str, value: str) -> str:
    return " set cfg(" + ",".join(_literal(part) for part in parts) + ")=" + _literal(value)


def _overhead(global_name: str, *fixed: bytes, variables: int = 1) -> int:
    return len(global_name) + 2 + sum(2 * len(part) + 2 for part in fixed) + 2 * variables


def _decimal_config(name: str, rule: dict[str, Any]) -> list[str]:
    allowed = (_RULES - {"minLength", "maxLength"}) | {"anyOf", "max_digits", "decimal_places"}
    if rule.keys() - allowed or rule.get("anyOf") != [{"type": "number"}, {"type": "string"}]:
        raise SchemaError(f"M writer cannot enforce the Decimal declaration of field {name!r}")
    result = [_set("tag", name, value="d")]
    for constraint, code in {"max_digits": "digits", "decimal_places": "places"}.items():
        if constraint in rule:
            bound = rule[constraint]
            if type(bound) is not int or not int(code == "digits") <= bound <= 32766:
                raise SchemaError("M Decimal precision limits must be bounded nonnegative integers")
            result.append(_set(code, name, value=str(bound)))
    for constraint, code in _BOUNDS.items():
        if constraint in rule:
            try:
                bound = str(units(Decimal(rule[constraint])))
            except ValueError:
                raise SchemaError("M Decimal bounds exceed the exact fixed-unit domain") from None
            result.append(_set(code, name, value=bound))
    return result


def _temporal_config(name: str, rule: dict[str, Any]) -> list[str]:
    bounds = {"temporal_" + key: code for key, code in _BOUNDS.items()}
    allowed = {"codec", "type", "format", "default", "default_factory", "yhmf", "strict", *bounds}
    if rule.keys() - allowed:
        raise SchemaError(f"M writer cannot enforce the temporal declaration of field {name!r}")
    tag = "D" if rule["codec"] == "date-v1" else "T"
    result = [_set("tag", name, value=tag)]
    for constraint, code in bounds.items():
        if constraint in rule:
            result.append(_set(code, name, value=rule[constraint]))
    return result


def _scalar_config(name: str, rule: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    if rule.get("codec") in {"date-v1", "datetime-utc-v1"}:
        return _temporal_config(name, rule)
    if rule.get("codec") == "decimal":
        return _decimal_config(name, rule)
    if rule.keys() - _RULES or rule.get("codec") not in {"str", "int"}:
        raise SchemaError(f"M writer cannot enforce the declaration of field {name!r}")
    tag = "s" if rule["codec"] == "str" else "i"
    constraints = rule.keys() & {"minLength", "maxLength", *_BOUNDS}
    supported = {"minLength", "maxLength"} if tag == "s" else _BOUNDS.keys()
    if constraints - supported:
        raise SchemaError(f"M writer cannot enforce the constraints on field {name!r}")
    result = [_set("tag", name, value=tag)]
    if tag == "s":
        minimum = max(rule.get("minLength", 0), policy.get("str_min_length", 0))
        maximum = min(rule.get("maxLength", 32764), policy.get("str_max_length", 32764))
        result += [_set("min", name, value=str(minimum)), _set("max", name, value=str(maximum))]
    for constraint, code in _BOUNDS.items():
        if constraint in rule:
            number = Decimal(rule[constraint])
            try:
                encoded = value_key(number).decode("ascii")
            except ValueError:
                raise SchemaError(
                    "M writer numeric bounds exceed its exact numeric domain"
                ) from None
            result.append(_set(code, name, value=encoded))
    return result


def _choice_config(field: FieldSpec, rule: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    assert field.choices is not None
    values = field.choices.values
    primitive = type(values[0])
    if primitive not in {str, int} or any(
        len(value.encode("utf-8")) > 32764 if isinstance(value, str) else len(str(abs(value))) > 18
        for value in values
    ):
        raise SchemaError("M choices require bounded UTF-8 strings or canonical 18-digit integers")
    encoded = [
        canonical_json(encode_scalar(value).decode("utf-8")).decode("ascii") for value in values
    ]
    if len(values) > 256 or sum(map(len, encoded)) > 32764:
        raise SchemaError("M choice domains require at most 256 members and 32764 encoded bytes")
    scalar_rule = {key: value for key, value in rule.items() if key not in {"enum", "codec"}}
    scalar_rule["codec"] = primitive.__name__
    result = _scalar_config(field.name, scalar_rule, policy)
    result.append(_set("choicecount", field.name, value=str(len(values))))
    for ordinal, value in enumerate(encoded, 1):
        for offset in range(0, len(value), 256):
            result.append(
                _set(
                    "choice",
                    field.name,
                    str(ordinal),
                    str(offset // 256 + 1),
                    value=value[offset : offset + 256],
                )
            )
    return result


def _reference_config(
    model: type[Model],
    global_name: str,
    field: FieldSpec,
    rule: dict[str, Any],
    targets: dict[type, ModelSpec],
) -> list[str]:
    annotation = scalar_type(model.model_fields[field.name].annotation)
    if (
        rule.keys() - {"type", "default", "default_factory", "yhmf"}
        or rule.get("type") != "object"
        or annotation.model_config != Reference.model_config
        or set(annotation.model_fields) != {"key"}
        or annotation.model_fields["key"].annotation != Reference.model_fields["key"].annotation
        or annotation.model_fields["key"].metadata
        or annotation.model_fields["key"].validation_alias is not None
        or not supports_local_validation(annotation)
    ):
        raise SchemaError("M writer references require standard Reference validation")
    assert field.target is not None
    target = targets.get(field.target)
    if target is None:
        raise SchemaError("M writer reference target has no trusted schema binding")
    return [
        _set("tag", field.name, value="r"),
        _set("ref", field.name, value=target.global_name[1:]),
        _set(
            "refOverhead",
            field.name,
            value=str(
                _overhead(
                    "^YHMFRef",
                    target.global_name[1:].encode(),
                    global_name[1:].encode(),
                    b"0" * 64,
                    variables=2,
                )
            ),
        ),
    ]


def _nullable_rule(field: FieldSpec, rule: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    options = rule.get("anyOf", [])
    nullable = field.kind == "scalar" and len(options) == 2 and options[1] == {"type": "null"}
    if not nullable:
        return rule, False
    return {**options[0], **{key: value for key, value in rule.items() if key != "anyOf"}}, True


def _field_config(
    node: NodeSpec,
    global_name: str,
    name: str,
    rule: dict[str, Any],
    policy: dict[str, Any],
    targets: dict[type, ModelSpec],
) -> list[str]:
    field = next(field for field in node.fields if field.name == name)
    rule, nullable = _nullable_rule(field, rule)
    if field.kind not in {"scalar", "reference"} or field.index_codec not in {
        "equality-v1",
        "ordered-v1",
    }:
        raise SchemaError(f"M writer does not support field {name!r}")
    result = (
        _reference_config(node.model, global_name, field, rule, targets)
        if field.kind == "reference"
        else _choice_config(field, rule, policy)
        if field.choices is not None
        else _scalar_config(name, rule, policy)
    )
    if nullable:
        result.append(_set("nullable", name, value="1"))
    if field.index_codec == "ordered-v1":
        result.append(_set("ordered", name, value="1"))
        if rule.get("codec") == "decimal":
            result.append(_set("source", name, value="1"))
    if field.index or field.kind == "reference":
        location = canonical_json([name])
        digest = hashlib.sha256(location).hexdigest()
        result += [
            _set("location", name, value=location.decode("ascii")),
            _set("digest", name, value=digest),
            _set("unique", name, value=str(int(field.unique))),
            _set(
                "overhead",
                name,
                value=str(
                    _overhead(
                        "^YHMFIndex",
                        global_name[1:].encode(),
                        name.encode(),
                        digest.encode(),
                        variables=2,
                    )
                ),
            ),
        ]
    if field.index:
        result.append(_set("index", name, value="1"))
    return result


def _schema_config(spec: ModelSpec, prefix: tuple[str, ...] = ()) -> list[str]:
    result = [
        _set(*prefix, "global", value=spec.global_name[1:]),
        _set(*prefix, "fingerprint", value=spec.fingerprint.decode("ascii")),
    ]
    for key, value in metadata(spec).items():
        result.append(_set(*prefix, "meta", key.decode("ascii"), value=value.decode("ascii")))
    snapshot = spec.snapshot.decode("ascii")
    result.append(_set(*prefix, "snapshot", value=""))
    subscript = ",".join(_literal(part) for part in (*prefix, "snapshot"))
    for offset in range(0, len(snapshot), 256):
        result.append(
            f" set cfg({subscript})=cfg({subscript})_" + _literal(snapshot[offset : offset + 256])
        )
    return result
