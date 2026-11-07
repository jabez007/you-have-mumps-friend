# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed export of schema-bound, flat scalar/Reference M writers."""

import hashlib
import re
from decimal import Decimal
from importlib.resources import files
from typing import Any

from .canonical_schema import schema_document
from .codecs import canonical_json
from .decimal_math import units
from .errors import SchemaError
from .models import Model, Reference
from .ordered import value_key
from .records import metadata
from .registry import FieldSpec, ModelSpec, NodeSpec, scalar_type, supports_local_validation

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


def _field_config(
    node: NodeSpec,
    global_name: str,
    name: str,
    rule: dict[str, Any],
    policy: dict[str, Any],
    targets: dict[type, ModelSpec],
) -> list[str]:
    field = next(field for field in node.fields if field.name == name)
    if (
        field.kind not in {"scalar", "reference"}
        or field.choices is not None
        or field.index_codec != "equality-v1"
    ):
        raise SchemaError(f"M writer does not support field {name!r}")
    result = (
        _reference_config(node.model, global_name, field, rule, targets)
        if field.kind == "reference"
        else _scalar_config(name, rule, policy)
    )
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


def _template(name: str) -> tuple[str, str]:
    resources = files(__package__)
    result = resources.joinpath("m_writer.template").read_text()
    for marker, filename in (
        ("@MAINTENANCE@", "m_maintenance.template"),
        ("@DECIMAL@", "m_decimal.template"),
        ("@SCHEMA@", "m_schema.template"),
        ("@REFERENCES@", "m_references.template"),
    ):
        result = result.replace(marker, resources.joinpath(filename).read_text())
    labels = {
        line.split()[0].partition("(")[0]
        for line in result.splitlines()
        if line and not line[0].isspace()
    }
    # The filename names the M routine independently of its initial label.
    return result, "%YHMFentry" if name in labels else name


def source(spec: ModelSpec, name: str, *, targets: tuple[ModelSpec, ...] = ()) -> str:
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
    config = [_set("key", value=spec.key_field), *_schema_config(spec)]
    bindings = {target.model: target for target in targets}
    for target in targets:
        prefix = ("targets", target.global_name[1:])
        config.extend(_schema_config(target, prefix))
        config.extend(
            [
                _set(*prefix, "key", value=target.key_field),
                _set(
                    *prefix,
                    "keytag",
                    value="s"
                    if target.model.model_fields[target.key_field].annotation is str
                    else "i",
                ),
            ]
        )
    for field, rule in declaration["properties"].items():
        config.extend(_field_config(node, spec.global_name, field, rule, policy, bindings))
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
    template, entry = _template(name)
    return (
        entry
        + " ; SPDX-License-Identifier: GPL-3.0-or-later\n"
        + template.replace("@GLOBAL@", spec.global_name)
        .replace("@KEY@", _literal(spec.key_field))
        .replace("@CONFIG@", "\n".join(config))
    )
