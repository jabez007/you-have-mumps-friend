# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed selectors over compiled declarations; no record reads or class mutation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .codecs import Key, encode_key
from .errors import SchemaError
from .models import Model, Reference

if TYPE_CHECKING:
    from .owned import OwnedPath
    from .registry import ModelSpec, NodeSpec, Registry


@dataclass(frozen=True)
class ModelPath[T: Model]:
    """An owned address bound to one immutable root declaration."""

    root: type[Model]
    target: type[T]
    at: OwnedPath = field(repr=False)
    fingerprint: bytes = field(repr=False)


@dataclass(frozen=True)
class ReferenceFactory[T: Model, K: (str, int)]:
    """Construct existing Reference[T] values with a statically checked key type."""

    _registry: Registry = field(repr=False)
    _model: type[T]

    def __call__(self, key: K) -> Reference[T]:
        self._registry.spec(self._model).path(key)
        reference = Reference.__class_getitem__(self._model)
        if not isinstance(reference, type):
            raise SchemaError("Reference target is not fully resolved")
        return cast(type[Reference[T]], reference)(key=key)


@dataclass(frozen=True)
class _Leaf:
    owner: type[Model]
    at: OwnedPath
    token: object

    def __bool__(self) -> bool:
        raise SchemaError("Selectors support declaration access only, not value conditions")


class _Probe:
    def __init__(self, node: ModelSpec | NodeSpec, at: OwnedPath, token: object):
        self._node, self._at, self._token = node, at, token

    def __getattr__(self, name: str) -> Any:
        declared = next((field for field in self._node.fields if field.name == name), None)
        if declared is None:
            raise SchemaError(
                f"Selector field {name!r} is not declared on {self._node.model.__name__}"
            )
        at = self._at + (name,)
        if declared.node is None:
            return _Leaf(self._node.model, at, self._token)
        if declared.kind == "children":
            return _Children(declared.node, at, self._token)
        return _Probe(declared.node, at, self._token)

    def __bool__(self) -> bool:
        raise SchemaError("Selectors support declaration access only, not value conditions")


class _Children:
    def __init__(self, node: NodeSpec, at: OwnedPath, token: object):
        self._node, self._at, self._token = node, at, token

    def __getitem__(self, key: Key) -> _Probe:
        assert self._node.key_field is not None
        annotation = self._node.model.model_fields[self._node.key_field].annotation
        if type(key) is not annotation:
            raise SchemaError("Selector child key type differs from its declaration")
        encode_key(key)
        return _Probe(self._node, self._at + (key,), self._token)


def model_path[T: Model](spec: ModelSpec, select: Callable[[Any], T | None]) -> ModelPath[T]:
    token = object()
    result: Any = select(_Probe(spec, (), token))
    if not isinstance(result, _Probe) or result._token is not token:
        raise SchemaError("Select one owned model through declaration fields and child keys")
    return ModelPath(spec.model, cast(type[T], result._node.model), result._at, spec.fingerprint)


def bind(spec: ModelSpec, path: ModelPath[Any]) -> OwnedPath:
    if path.root is not spec.model or path.fingerprint != spec.fingerprint:
        raise SchemaError("Typed path belongs to a different model declaration")
    return path.at


def reference_factory[T: Model, K: (str, int)](
    registry: Registry, model: type[T], select: Callable[[T], K]
) -> ReferenceFactory[T, K]:
    spec, token = registry.spec(model), object()
    result: Any = select(cast(T, _Probe(spec, (), token)))
    if (
        not isinstance(result, _Leaf)
        or result.token is not token
        or result.owner is not model
        or result.at != (spec.key_field,)
    ):
        raise SchemaError("Select the target model's declared primary-key field")
    return ReferenceFactory(registry, model)
