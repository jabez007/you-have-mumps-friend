# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed direct addressing and validation policy for parent-owned values."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .codecs import Key, encode_key
from .errors import MutationError, NotFound, StorageError
from .records import MAX_CHILDREN
from .registry import FieldSpec, ModelSpec, NodeSpec
from .storage import Path, Store

if TYPE_CHECKING:
    from .repository import Database


type OwnedPath = tuple[Key, ...]


@dataclass(frozen=True)
class Owner:
    node: NodeSpec
    path: Path
    relative: tuple[bytes, ...]


def require_local_validation(node: NodeSpec) -> None:
    """Reject ancestor validation whose dependency on descendants is unknowable."""
    if not node.local_validation:
        raise MutationError(
            "Partial mutation cannot validate this owner's custom validators; "
            "use whole-aggregate save inside atomic"
        )


def collection_count(store: Store, path: Path) -> int:
    marker = store.get(path)
    if marker == b"n:":
        if store.children(path, limit=1):
            raise StorageError("Null collection has unexpected descendants")
        raise NotFound("Owned collection is null")
    try:
        if marker is None or not marker.startswith(b"c:"):
            raise ValueError
        count = int(marker[2:])
        if marker != b"c:" + str(count).encode("ascii") or not 0 <= count <= MAX_CHILDREN:
            raise ValueError
    except ValueError as exc:
        raise StorageError("Invalid or missing child collection marker") from exc
    return count


def field_named(node: NodeSpec, name: Key) -> FieldSpec:
    if type(name) is not str:
        raise MutationError("Owned paths require a declared field name")
    field = next((field for field in node.fields if field.name == name), None)
    if field is None:
        raise MutationError("Owned path names an undeclared field")
    return field


def require_object(store: Store, path: Path) -> None:
    marker = store.get(path)
    if marker in (None, b"n:"):
        if store.children(path, limit=1):
            raise StorageError("Missing owned marker has unexpected descendants")
        raise NotFound("Owned value does not exist")
    if marker != b"o:1":
        raise StorageError("Invalid owned object marker")


def resolve(
    tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath, *, mutate: bool = False
) -> Owner:
    if type(at) is not tuple:
        raise MutationError("Owned paths must be tuples")
    root = spec.path(key)
    spec = tx._check_schema(spec)
    if not tx._exists(spec, root):
        raise NotFound("Root record does not exist")
    owner = Owner(
        NodeSpec(spec.model, spec.key_field, spec.fields, spec.local_validation), root, ()
    )
    remaining = at
    while remaining:
        if mutate:
            require_local_validation(owner.node)
        owner, remaining = _step(tx.store, owner, remaining)
    return owner


def _step(store: Store, owner: Owner, remaining: OwnedPath) -> tuple[Owner, OwnedPath]:
    field = field_named(owner.node, remaining[0])
    if field.node is None:
        raise MutationError("Owned path must address an embedded object or keyed child")
    parts: tuple[bytes, ...] = (field.name.encode("ascii"),)
    consumed = 1
    if field.kind == "children":
        count = collection_count(store, owner.path.child(*parts))
        if len(remaining) < 2:
            raise MutationError("A child collection requires its parent-scoped key")
        child_key = remaining[1]
        assert field.node.key_field is not None
        if type(child_key) is not field.node.model.model_fields[field.node.key_field].annotation:
            raise MutationError("Owned child key type does not match its declaration")
        parts += (encode_key(child_key),)
        consumed = 2
        if count == 0 and store.get(owner.path.child(*parts)) is not None:
            raise StorageError("Empty collection contains an owned child")
    path = owner.path.child(*parts)
    require_object(store, path)
    return Owner(field.node, path, owner.relative + parts), remaining[consumed:]
