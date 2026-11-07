# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated model declarations and canonical installed-schema identities."""

import hashlib
import re
import types
from collections.abc import Callable
from dataclasses import dataclass, replace
from dataclasses import field as compiled_field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .canonical_schema import schema_bytes
from .choices import Choices, compile_choices
from .codecs import LAYOUT_VERSION
from .errors import SchemaError
from .models import Children, Model, Reference
from .schemas import canonical_json
from .selectors import ReferenceFactory, reference_factory
from .storage import Path

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_SCALARS = {str, int, bool, float, Decimal, bytes, date, datetime, UUID}


def scalar_type(annotation: Any) -> Any:
    """Unwrap nullable scalar types without coercing ambiguous unions."""
    if get_origin(annotation) in (Union, types.UnionType):
        options = [t for t in get_args(annotation) if t is not type(None)]
        if len(options) == 1:
            return options[0]
    return annotation


def _nested_model_schema(value: dict[str, Any], model: type[Model]) -> bool:
    inner = value
    while str(inner.get("type", "")).startswith("function-"):
        wrapped = inner.get("schema")
        if not isinstance(wrapped, dict):
            return False
        inner = wrapped
    nested = inner.get("cls")
    return (
        isinstance(nested, type)
        and issubclass(nested, Model)
        and nested is not model
        and value.get("ref") is not None
        and value.get("ref") == nested.__pydantic_core_schema__.get("ref")
    )


def _has_functions(value: Any, model: type[Model]) -> bool:
    if isinstance(value, list):
        return any(_has_functions(item, model) for item in value)
    if not isinstance(value, dict):
        return False
    nested = value.get("cls")
    if value.get("type") == "model" and nested is not model:
        return False
    if str(value.get("type", "")).startswith("function-"):
        return not _nested_model_schema(value, model)
    # Ordinary schema dictionaries have no wrappers to peel. Keep the existing
    # treatment of foreign cls/ref descriptors without probing every dictionary.
    if nested is not None and nested is not model and _nested_model_schema(value, model):
        return False
    return any(_has_functions(item, model) for item in value.values())


def supports_local_validation(model: type[Model]) -> bool:
    """Classify declarations whose partial validation cannot depend on descendants."""
    return not (
        model.__pydantic_post_init__
        or model.__pydantic_custom_init__
        or _has_functions(model.__pydantic_core_schema__, model)
    )


def field_adapter(model: type[Model], name: str) -> TypeAdapter[Any]:
    """Compile a field's declared rules and owner config without model validators."""
    field = model.model_fields[name]
    annotation = scalar_type(field.annotation)
    # A Reference is itself a Pydantic model and owns its configuration. Pydantic
    # rejects an adapter config override for BaseModel types.
    owns_config = isinstance(annotation, type) and issubclass(annotation, BaseModel)
    config = None if owns_config else model.model_config
    return TypeAdapter(field.rebuild_annotation(), config=config)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Literal["scalar", "list", "embedded", "children", "reference"]
    node: "NodeSpec | None" = None
    target: type[Model] | None = None
    target_key: str | None = None
    index: bool = False
    unique: bool = False
    adapter: TypeAdapter[Any] | None = compiled_field(default=None, repr=False, compare=False)
    choices: Choices | None = compiled_field(default=None, repr=False, compare=False)
    item: "FieldSpec | None" = None
    on_delete: str = "restrict"


@dataclass(frozen=True)
class NodeSpec:
    model: type[Model]
    key_field: str | None
    fields: tuple[FieldSpec, ...]
    local_validation: bool = False


@dataclass(frozen=True)
class ModelSpec:
    model: type[Model]
    global_name: str
    key_field: str
    fields: tuple[FieldSpec, ...]
    snapshot: bytes
    fingerprint: bytes
    local_validation: bool = False

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


def _field_metadata(model: type[Model], name: str) -> dict[str, Any]:
    field = model.model_fields[name]
    extra = field.json_schema_extra
    metadata = extra.get("yhmf", {}) if isinstance(extra, dict) else {}
    if not isinstance(metadata, dict):
        raise SchemaError("Invalid mapper metadata")
    return metadata


def _child_spec(
    annotation: Any,
    name: str,
    metadata: dict[str, Any],
    ancestors: tuple[type[Model], ...],
) -> FieldSpec:
    child_model = get_args(annotation)[0]
    if not isinstance(child_model, type) or not issubclass(child_model, Model):
        raise SchemaError("Children requires a model type")
    node = _node_spec(child_model, keyed=True, ancestors=ancestors)
    if metadata.get("children_key") != node.key_field:
        raise SchemaError("ChildField key must match the child's primary-key field")
    return FieldSpec(name, "children", node)


def _field_spec(model: type[Model], name: str, ancestors: tuple[type[Model], ...]) -> FieldSpec:
    annotation = scalar_type(model.model_fields[name].annotation)
    metadata = _field_metadata(model, name)
    spec = _field_type_spec(annotation, name, metadata, ancestors)
    if spec.on_delete == "set_null" and type(None) not in get_args(
        model.model_fields[name].annotation
    ):
        raise SchemaError("set_null requires a nullable Reference field")
    choices = spec.choices if spec.item is None else spec.item.choices
    if choices is not None and choices.kind == "enum" and model.model_config.get("use_enum_values"):
        raise SchemaError("Enum fields do not support use_enum_values=True")
    spec = _indexed_spec(spec, metadata)
    return replace(spec, adapter=field_adapter(model, name) if spec.node is None else None)


def _indexed_spec(spec: FieldSpec, metadata: dict[str, Any]) -> FieldSpec:
    indexed, unique = bool(metadata.get("index")), bool(metadata.get("unique"))
    if (indexed or unique) and spec.kind not in {"scalar", "list", "reference"}:
        raise SchemaError("Indexes require scalar, scalar list, or Reference fields")
    return replace(spec, index=indexed or unique, unique=unique)


def _field_type_spec(
    annotation: Any,
    name: str,
    metadata: dict[str, Any],
    ancestors: tuple[type[Model], ...],
) -> FieldSpec:
    if get_origin(annotation) is Children:
        return _child_spec(annotation, name, metadata, ancestors)
    if "children_key" in metadata:
        raise SchemaError("ChildField requires a Children[Model] annotation")
    if get_origin(annotation) is list:
        return _list_spec(annotation, name)
    if isinstance(annotation, type) and issubclass(annotation, Reference):
        target = reference_target(annotation)
        policy = metadata.get("on_delete", "restrict")
        if policy not in ("restrict", "set_null", "cascade"):
            raise SchemaError("Invalid Reference on_delete policy")
        return FieldSpec(
            name, "reference", target=target, target_key=_key_field(target), on_delete=policy
        )
    if metadata.get("on_delete"):
        raise SchemaError("on_delete requires a Reference field")
    if isinstance(annotation, type) and issubclass(annotation, Model):
        return FieldSpec(name, "embedded", _node_spec(annotation, keyed=False, ancestors=ancestors))
    choices = compile_choices(annotation)
    if annotation not in _SCALARS and choices is None:
        raise SchemaError("Unsupported field type; use scalar, Model, or Children[Model]")
    return FieldSpec(name, "scalar", choices=choices)


def _list_spec(annotation: Any, name: str) -> FieldSpec:
    args = get_args(annotation)
    if len(args) != 1:
        raise SchemaError("Scalar lists require an explicit item type")
    item_type = scalar_type(args[0])
    choices = compile_choices(item_type)
    if item_type not in _SCALARS and choices is None:
        raise SchemaError("Scalar list items require plain scalar or Enum/Literal types")
    return FieldSpec(name, "list", item=FieldSpec(name, "scalar", choices=choices))


def reference_target(annotation: type[Reference[Any]]) -> type[Model]:
    args = annotation.__pydantic_generic_metadata__["args"]
    if len(args) != 1 or not isinstance(args[0], type) or not issubclass(args[0], Model):
        raise SchemaError("References require a concrete target Model")
    target = args[0]
    _application_global(target)
    return target


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
    if model.model_config.get("extra") == "allow":
        raise SchemaError("Models with extra='allow' cannot be persisted; declare stored fields")
    for computed in model.model_computed_fields.values():
        extra = computed.json_schema_extra
        if isinstance(extra, dict) and "yhmf" in extra:
            raise SchemaError("Computed fields are virtual; persistence metadata is not supported")
    for name, field in model.model_fields.items():
        if not _NAME.fullmatch(name) or any(
            (field.alias, field.validation_alias, field.serialization_alias)
        ):
            raise SchemaError("Fields must have plain names without aliases")
    key = _key_field(model) if keyed else None
    fields = tuple(_field_spec(model, name, ancestors + (model,)) for name in model.model_fields)
    return NodeSpec(model, key, fields, supports_local_validation(model))


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


def _application_global(model: type[Model]) -> str:
    global_name = model.__global__
    if global_name is None or global_name in {
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFRef",
        "^YHMFUnique",
        "^YHMFMigration",
    }:
        raise SchemaError("Declare an application-owned __global__")
    Path(global_name)
    return global_name


def reference_fields(fields: tuple[FieldSpec, ...]) -> tuple[FieldSpec, ...]:
    result = []
    for field in fields:
        if field.kind == "reference":
            result.append(field)
        elif field.node is not None:
            result.extend(reference_fields(field.node.fields))
    return tuple(result)


def _reference_declarations(
    fields: tuple[FieldSpec, ...], prefix: tuple[str, ...] = ()
) -> list[Any]:
    result = []
    for field in fields:
        path = prefix + (field.name,)
        if field.target is not None:
            result.append(
                {
                    "path": path,
                    "global": _application_global(field.target),
                    "key": field.target_key,
                    "on_delete": field.on_delete,
                }
            )
        elif field.node is not None:
            result.extend(_reference_declarations(field.node.fields, path))
    return result


def _legacy_snapshot(node: NodeSpec, global_name: str) -> bytes:
    # Preserve the original ordering and JSON Schema exactly for old databases.
    model = node.model
    declaration = {
        "layout": LAYOUT_VERSION,
        "global": global_name,
        "version": model.__schema_version__,
        "key": node.key_field,
        "declaration": model.model_json_schema(),
    }
    hierarchy = _hierarchy(node.fields)
    if hierarchy:
        declaration["hierarchy"] = hierarchy
    references = _reference_declarations(node.fields)
    if references:
        declaration["references"] = references
    return canonical_json(declaration)


def _make_spec(model: type[Model], *, legacy: bool = False) -> ModelSpec:
    model.model_rebuild()
    global_name = _application_global(model)
    node = _node_spec(model, keyed=True, ancestors=())
    key = _key_field(model)
    try:
        snapshot = (
            _legacy_snapshot(node, global_name) if legacy else schema_bytes(node, global_name)
        )
    except SchemaError:
        raise
    except (TypeError, ValueError) as exc:
        raise SchemaError("Model declarations must produce finite canonical JSON") from exc
    return ModelSpec(
        model,
        global_name,
        key,
        node.fields,
        snapshot,
        hashlib.sha256(snapshot).hexdigest().encode("ascii"),
        node.local_validation,
    )


@dataclass(frozen=True)
class _Declaration:
    owner: type[Model]
    schema: Any
    config: ConfigDict
    global_name: str | None
    version: int

    def unchanged(self) -> bool:
        return (
            self.owner.__pydantic_core_schema__ is self.schema
            and self.owner.model_config == self.config
            and self.owner.__global__ == self.global_name
            and type(self.owner.__schema_version__) is int
            and self.owner.__schema_version__ == self.version
        )


def _declaration_schemas(
    model: type[Model], fields: tuple[FieldSpec, ...]
) -> tuple[_Declaration, ...]:
    result = [
        _Declaration(
            model,
            model.__pydantic_core_schema__,
            model.model_config.copy(),
            model.__global__,
            model.__schema_version__,
        )
    ]
    for item in fields:
        if item.node is not None:
            result.extend(_declaration_schemas(item.node.model, item.node.fields))
    return tuple(result)


class Registry:
    """Explicitly register top-level models and reject overlapping global ownership."""

    def __init__(self, *models: type[Model]) -> None:
        self._specs: dict[type[Model], ModelSpec] = {}
        self._globals: dict[str, type[Model]] = {}
        self._schemas: dict[type[Model], tuple[_Declaration, ...]] = {}
        for model in models:
            self.register(model)
        self.validate_references()

    def validate_references(self) -> None:
        for spec in self._specs.values():
            for field in reference_fields(spec.fields):
                assert field.target is not None
                if field.target not in self._specs:
                    raise SchemaError(
                        f"{spec.model.__name__}.{field.name} references unregistered target "
                        f"{field.target.__name__}; register that exact target declaration or "
                        "redeclare the reference against its registered replacement"
                    )

    def reference[M: Model, K: (str, int)](
        self, model: type[M], key: Callable[[M], K]
    ) -> ReferenceFactory[M, K]:
        """Infer a reference constructor's key type from its selected primary key."""
        return reference_factory(self, model, key)

    def register(self, model: type[Model]) -> ModelSpec:
        if model in self._specs:
            return self.spec(model)
        spec = _make_spec(model)
        if spec.global_name in self._globals:
            raise SchemaError("A global can belong to only one registered model")
        self._globals[spec.global_name] = model
        self._specs[model] = spec
        self._schemas[model] = _declaration_schemas(model, spec.fields)
        return spec

    def spec(self, model: type[Model]) -> ModelSpec:
        try:
            spec = self._specs[model]
        except KeyError as exc:
            raise SchemaError("Register the model before database access") from exc
        if any(not declaration.unchanged() for declaration in self._schemas[model]):
            raise SchemaError(
                "Model declaration changed or was rebuilt after registration; "
                "create a fresh registry"
            )
        return spec

    def _spec_for_global(self, global_name: str) -> ModelSpec | None:
        model = self._globals.get(global_name)
        return None if model is None else self.spec(model)
