# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed export of schema-bound transactional M writers."""

import re
from importlib.resources import files

from .canonical_schema import schema_document
from .errors import SchemaError
from .m_hierarchy_config import compile_hierarchy
from .m_writer_config import _field_config, _literal, _overhead, _schema_config, _set
from .registry import ModelSpec, NodeSpec

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]{0,30}\Z")


def _template(name: str) -> tuple[str, str]:
    resources = files(__package__)
    result = resources.joinpath("m_writer.template").read_text()
    for marker, filename in (
        ("@MAINTENANCE@", "m_maintenance.template"),
        ("@DECIMAL@", "m_decimal.template"),
        ("@TEMPORAL@", "m_temporal.template"),
        ("@STRINGS@", "m_strings.template"),
        ("@ORDERED@", "m_ordered.template"),
        ("@SUMMATH@", "m_sum_math.template"),
        ("@SHA256@", "m_sha256.template"),
        ("@HIERARCHY@", "m_hierarchy.template"),
        ("@EMBEDDED@", "m_embedded.template"),
        ("@TREEDELETE@", "m_tree_delete.template"),
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
    """Export source; first inserts require an empty unmanaged application global."""
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
    if any(field.node is not None for field in spec.fields):
        config.extend(compile_hierarchy(spec, node, declaration, bindings))
    else:
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
