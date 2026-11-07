# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed export of schema-bound, flat ASCII str/int/Decimal M writers."""

import hashlib
import re
from decimal import Decimal
from importlib.resources import files
from typing import Any

from .canonical_schema import schema_document
from .codecs import canonical_json
from .decimal_math import units
from .errors import SchemaError
from .ordered import value_key
from .records import metadata
from .registry import ModelSpec, NodeSpec

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,30}\Z")
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


def _scalar_config(name: str, rule: dict[str, Any], policy: dict[str, Any]) -> list[str]:
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


def _field_config(
    spec: ModelSpec, name: str, rule: dict[str, Any], policy: dict[str, Any]
) -> list[str]:
    field = next(field for field in spec.fields if field.name == name)
    if field.kind != "scalar" or field.choices is not None or field.index_codec != "equality-v1":
        raise SchemaError(f"M writer does not support field {name!r}")
    result = _scalar_config(name, rule, policy)
    if field.index:
        location = canonical_json([name])
        digest = hashlib.sha256(location).hexdigest()
        result += [
            _set("index", name, value="1"),
            _set("location", name, value=location.decode("ascii")),
            _set("digest", name, value=digest),
            _set("unique", name, value=str(int(field.unique))),
            _set(
                "overhead",
                name,
                value=str(
                    _overhead(
                        "^YHMFIndex",
                        spec.global_name[1:].encode(),
                        name.encode(),
                        digest.encode(),
                        variables=2,
                    )
                ),
            ),
        ]
    return result


def _template(name: str) -> str:
    resources = files(__package__)
    result = resources.joinpath("m_writer.template").read_text()
    for marker, filename in (
        ("@MAINTENANCE@", "m_maintenance.template"),
        ("@DECIMAL@", "m_decimal.template"),
    ):
        result = result.replace(marker, resources.joinpath(filename).read_text())
    labels = {
        line.split()[0].partition("(")[0]
        for line in result.splitlines()
        if line and not line[0].isspace()
    }
    if name in labels:
        raise SchemaError("M routine name collides with a generated entry label")
    return result


def source(spec: ModelSpec, name: str) -> str:
    """Export source only. The routine installs metadata only on its first valid insert."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise SchemaError("M routine names require an ASCII letter and at most 30 letters/digits")
    if not spec.local_validation or len(spec.fields) > 16:
        raise SchemaError("M writers require at most 16 fields and no custom validation")
    node = NodeSpec(spec.model, spec.key_field, spec.fields, spec.local_validation)
    declaration = schema_document(node, spec.global_name)["declaration"]
    policy = declaration.get("validation_policy", {})
    if policy.keys() - {"str_min_length", "str_max_length"}:
        raise SchemaError("M writer does not support this model's validation policy")
    config = [
        _set("global", value=spec.global_name[1:]),
        _set("key", value=spec.key_field),
        _set("fingerprint", value=spec.fingerprint.decode("ascii")),
    ]
    for key, value in metadata(spec).items():
        config.append(_set("meta", key.decode("ascii"), value=value.decode("ascii")))
    for field, rule in declaration["properties"].items():
        config.extend(_field_config(spec, field, rule, policy))
    config.append(
        _set(
            "dataOverhead",
            value=str(
                max(_overhead(spec.global_name, field.name.encode()) for field in spec.fields)
                + 2 * len(b"fingerprint")
                + 2 * len(b"%m")
                + 4
            ),
        )
    )
    snapshot = spec.snapshot.decode("ascii")
    config.append(_set("snapshot", value=""))
    for offset in range(0, len(snapshot), 256):
        config.append(
            ' set cfg("snapshot")=cfg("snapshot")_' + _literal(snapshot[offset : offset + 256])
        )
    template = _template(name)
    return (
        name
        + " ; SPDX-License-Identifier: GPL-3.0-or-later\n"
        + template.replace("@GLOBAL@", spec.global_name)
        .replace("@KEY@", _literal(spec.key_field))
        .replace("@CONFIG@", "\n".join(config))
    )
