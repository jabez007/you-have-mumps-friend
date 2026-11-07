# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile bounded keyed ownership using the ordinary M scalar/reference rules."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .errors import SchemaError
from .m_writer_config import _field_config, _literal, _overhead, _set
from .registry import FieldSpec, ModelSpec, NodeSpec


@dataclass(frozen=True)
class _Owner:
    path: tuple[str, ...]
    node: NodeSpec
    declaration: dict[str, Any]

    @property
    def name(self) -> str:
        return ".".join(self.path)


def _owners(
    node: NodeSpec, declaration: dict[str, Any], path: tuple[str, ...] = ()
) -> Iterator[_Owner]:
    yield _Owner(path, node, declaration)
    for field in node.fields:
        if field.node is None:
            continue
        rule = declaration["properties"][field.name]
        if field.kind != "children" or rule.get("type") != "object":
            raise SchemaError("M writers require nonnullable Children for owned collections")
        yield from _owners(field.node, rule["additionalProperties"], path + (field.name,))


def _nested(prefix: tuple[str, ...], constants: list[str]) -> list[str]:
    """Qualify constant assignments; field rules still have one implementation."""
    stem = " set cfg(" + ",".join(_literal(part) for part in prefix) + ","
    assert all(line.startswith(" set cfg(") for line in constants)
    return [stem + line.removeprefix(" set cfg(") for line in constants]


def _collection(owner: _Owner, field: FieldSpec, rule: dict[str, Any]) -> list[str]:
    if rule.keys() - {
        "type",
        "additionalProperties",
        "yhmf",
        "default",
        "default_factory",
        "minLength",
        "maxLength",
    }:
        raise SchemaError("M writer cannot enforce the collection declaration")
    low, high = rule.get("minLength", 0), min(rule.get("maxLength", 1000), 1000)
    if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 1000:
        raise SchemaError("M collection limits must fit the bounded child domain")
    return [
        _set("children", field.name, value=".".join(owner.path + (field.name,))),
        _set("minchildren", field.name, value=str(low)),
        _set("maxchildren", field.name, value=str(high)),
    ]


def _node_config(owner: _Owner, spec: ModelSpec, targets: dict[type, ModelSpec]) -> list[str]:
    node, declaration = owner.node, owner.declaration
    if not node.local_validation or len(node.fields) > 16 or node.key_field is None:
        raise SchemaError(
            "M owned nodes require at most 16 fields, a key, and no custom validation"
        )
    policy = declaration.get("validation_policy", {})
    if policy.keys() - {"str_min_length", "str_max_length"}:
        raise SchemaError("M writer does not support this owner's validation policy")
    result = [
        _set("key", value=node.key_field),
        _set("width", value=str(len(node.fields))),
        _set("leaf", value=str(int(all(field.node is None for field in node.fields)))),
    ]
    for field in node.fields:
        rule = declaration["properties"][field.name]
        if field.node is not None:
            result.extend(_collection(owner, field, rule))
            continue
        result.extend(_field_config(node, spec.global_name, field.name, rule, policy, targets))
        dotted = ".".join(owner.path + (field.name,))
        result.append(_set("indexname", field.name, value=dotted))
        if field.index:
            result.append(
                _set(
                    "overhead",
                    field.name,
                    value=str(
                        _overhead(
                            "^YHMFIndex",
                            spec.global_name[1:].encode(),
                            dotted.encode(),
                            b"0" * 64,
                            variables=2,
                        )
                    ),
                )
            )
        if field.sum_of is not None:
            result.append(_set("derived", field.name, value="1"))
    return result


def compile_hierarchy(
    spec: ModelSpec, node: NodeSpec, declaration: dict[str, Any], targets: dict[type, ModelSpec]
) -> list[str]:
    """Reuse existing codecs and identities; no record/schema installation occurs."""
    owners = tuple(_owners(node, declaration))
    if len(owners) > 64:
        raise SchemaError("M writers support at most 64 owned declarations")
    result = [_set("hierarchy", value="1")]
    for owner in owners:
        result.extend(_nested(("nodes", owner.name), _node_config(owner, spec, targets)))
    ordinal = 0
    for owner in owners:
        for field in owner.node.fields:
            if field.sum_of is None:
                continue
            ordinal += 1
            source = owner.path + tuple(field.sum_of.split("."))
            source_owner = ".".join(source[:-1])
            prefix = ("sumtargets", str(ordinal))
            result.extend(
                [
                    _set(*prefix, "owner", value=owner.name),
                    _set(*prefix, "depth", value=str(2 * len(owner.path))),
                    _set(*prefix, "field", value=field.name),
                    _set(*prefix, "sourceOwner", value=source_owner),
                    _set(*prefix, "sourceField", value=source[-1]),
                    _set("nodes", source_owner, "source", source[-1], value="1"),
                ]
            )
    return result
