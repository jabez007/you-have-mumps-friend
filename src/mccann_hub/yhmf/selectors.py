# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed selectors over compiled declarations; no record reads or class mutation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import TYPE_CHECKING, Any, cast

from .codecs import Key, encode_key
from .errors import SchemaError
from .models import Children, Model, Reference

if TYPE_CHECKING:
    from .owned import OwnedPath
    from .registry import ModelSpec, NodeSpec, Registry


@dataclass(frozen=True)
class ModelPath[T: Model]:
    """An owned address bound to one immutable root declaration."""

    root: type[Model]
    target: type[T]
    at: OwnedPath = dataclass_field(repr=False)
    fingerprint: bytes = dataclass_field(repr=False)


@dataclass(frozen=True)
class QueryField[M: Model, T]:
    """A typed field declaration, independent of any concrete child key."""

    root: type[M]
    name: str
    fingerprint: bytes = dataclass_field(repr=False)

    def eq(self, value: T) -> FieldPredicate[M]:
        """Build an equality predicate; the query validates its declared index."""
        return FieldPredicate(self, value)


@dataclass(frozen=True)
class FieldPredicate[M: Model]:
    """A typed equality whose runtime validation uses the ordinary query planner."""

    field: QueryField[M, Any]
    value: Any = dataclass_field(repr=False)


@dataclass(frozen=True)
class ReferenceFactory[T: Model, K: (str, int)]:
    """Construct existing Reference[T] values with a statically checked key type."""

    _registry: Registry = dataclass_field(repr=False)
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
    kind: str

    def __bool__(self) -> bool:
        raise SchemaError("Selectors support declaration access only, not value conditions")


class _Probe:
    def __init__(
        self, node: ModelSpec | NodeSpec, at: OwnedPath, token: object, declaration: bool = False
    ):
        self._node, self._at, self._token = node, at, token
        self._declaration = declaration

    def __getattr__(self, name: str) -> Any:
        declared = next((field for field in self._node.fields if field.name == name), None)
        if declared is None:
            raise SchemaError(
                f"Selector field {name!r} is not declared on {self._node.model.__name__}"
            )
        at = self._at + (name,)
        if declared.node is None:
            return _Leaf(self._node.model, at, self._token, declared.kind)
        if declared.kind == "children":
            return _Children(declared.node, at, self._token, self._declaration)
        return _Probe(declared.node, at, self._token, self._declaration)

    def __bool__(self) -> bool:
        raise SchemaError("Selectors support declaration access only, not value conditions")


class _Children:
    def __init__(self, node: NodeSpec, at: OwnedPath, token: object, declaration: bool = False):
        self._node, self._at, self._token = node, at, token
        self._declaration = declaration

    def __getitem__(self, key: Key) -> _Probe:
        if self._declaration:
            raise SchemaError("Query field selectors use each(children), not a concrete child key")
        assert self._node.key_field is not None
        annotation = self._node.model.model_fields[self._node.key_field].annotation
        if type(key) is not annotation:
            raise SchemaError("Selector child key type differs from its declaration")
        encode_key(key)
        return _Probe(self._node, self._at + (key,), self._token)


def each[T: Model](children: Children[T]) -> T:
    """Select a child declaration inside Database.field; never iterate records."""
    if not isinstance(children, _Children) or not children._declaration:
        raise SchemaError("each() is only available inside a query field selector")
    return cast(T, _Probe(children._node, children._at, children._token, True))


def query_field[M: Model, T](spec: ModelSpec, select: Callable[[M], T]) -> QueryField[M, T]:
    token = object()
    result: Any = select(cast(M, _Probe(spec, (), token, True)))
    if (
        not isinstance(result, _Leaf)
        or result.token is not token
        or result.kind not in ("scalar", "reference")
    ):
        raise SchemaError("Select one scalar or reference declaration for a query field")
    return QueryField(cast(type[M], spec.model), ".".join(map(str, result.at)), spec.fingerprint)


def bind_field(spec: ModelSpec, selected: QueryField[Any, Any]) -> str:
    if selected.root is not spec.model or selected.fingerprint != spec.fingerprint:
        raise SchemaError("Query field belongs to a different model declaration")
    return selected.name


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
