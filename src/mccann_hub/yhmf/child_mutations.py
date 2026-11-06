# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct keyed ownership changes using the existing codecs and secondary entries."""

from typing import TYPE_CHECKING

from .codecs import Key, encode_key
from .errors import AlreadyExists, MutationError, StorageError
from .models import Model
from .mutations import replace_secondary
from .owned import (
    OwnedPath,
    Owner,
    collection_count,
    field_named,
    require_local_validation,
    require_object,
    resolve,
)
from .records import (
    MAX_CHILDREN,
    MAX_RECORD_NODES,
    input_values,
    node_nodes,
    node_values,
    walk_values,
)
from .registry import FieldSpec, ModelSpec
from .storage import Path, Store

if TYPE_CHECKING:
    from .repository import Database


def _collection(
    tx: "Database", spec: ModelSpec, key: Key, collection: OwnedPath
) -> tuple[Owner, FieldSpec, Path, int]:
    if type(collection) is not tuple or not collection:
        raise MutationError("Collection must be a nonempty owned field path")
    owner = resolve(tx, spec, key, collection[:-1], mutate=True)
    require_local_validation(owner.node.model)
    field = field_named(owner.node, collection[-1])
    if field.kind != "children":
        raise MutationError("Collection path must name a keyed Children field")
    path = owner.path.child(field.name.encode("ascii"))
    return owner, field, path, collection_count(tx.store, path)


def _length(owner: Owner, field: FieldSpec, count: int) -> None:
    if not 0 <= count <= MAX_CHILDREN:
        raise StorageError("Child collection exceeds 1000 entries")
    info = owner.node.model.model_fields[field.name]
    for constraint in info.metadata:
        minimum = getattr(constraint, "min_length", None)
        maximum = getattr(constraint, "max_length", None)
        if (minimum is not None and count < minimum) or (maximum is not None and count > maximum):
            raise MutationError("Child mutation violates collection length constraints")


def _owned_nodes(store: Store, field: FieldSpec, path: Path) -> tuple[tuple[Path, ...], int]:
    if store.get(path) == b"n:":
        if store.children(path, limit=1):
            raise StorageError("Null owned value has unexpected descendants")
        return (), 0
    if field.kind == "embedded":
        require_object(store, path)
        return (path,), 0
    count = collection_count(store, path)
    assert field.node is not None
    if all(nested.node is None for nested in field.node.fields):
        # c:N is the authoritative transactional count. Every fixed-width child
        # contributes one object marker plus one value per declared leaf field.
        return (), count * (1 + len(field.node.fields))
    keys = store.children(path, limit=MAX_CHILDREN + 1)
    if len(keys) != count:
        raise StorageError("Child collection count disagrees with its keys")
    children = tuple(path.child(key) for key in keys)
    for child in children:
        require_object(store, child)
    return children, count


def _value_count(store: Store, spec: ModelSpec, root: Path, allowance: int) -> None:
    # Fixed-width children use c:N without reading siblings. Only variable-width
    # owned branches need traversal. This is a budget check, not a raw-data audit.
    count = 4  # Existing layout-one root metadata, checked before this traversal.
    pending = [(field, root.child(field.name.encode("ascii"))) for field in spec.fields]
    while pending:
        field, path = pending.pop()
        count += 1
        if count + len(pending) > allowance:
            raise StorageError("Child insertion exceeds the root record node budget")
        if field.node is not None:
            children, markers = _owned_nodes(store, field, path)
            count += markers
            pending.extend(
                (nested, child.child(nested.name.encode("ascii")))
                for child in children
                for nested in field.node.fields
            )
    if count > allowance:
        raise StorageError("Child insertion exceeds the root record node budget")


def insert(tx: "Database", spec: ModelSpec, key: Key, collection: OwnedPath, record: Model) -> None:
    owner, field, path, count = _collection(tx, spec, key, collection)
    assert field.node is not None and field.node.key_field is not None
    if not isinstance(record, field.node.model):
        raise MutationError("Inserted child type does not match its declared model")
    validated = field.node.model.model_validate(input_values(field.node.fields, record))
    child_key = getattr(validated, field.node.key_field)
    if type(child_key) is not field.node.model.model_fields[field.node.key_field].annotation:
        raise MutationError("Child identity type does not match its declaration")
    child = path.child(encode_key(child_key))
    if tx.store.get(child) is not None or tx.store.children(child, limit=1):
        raise AlreadyExists("Child insertion cannot replace existing data")
    _length(owner, field, count + 1)
    nodes = {child: b"o:1"} | node_nodes(field.node.fields, child, validated)
    _value_count(tx.store, spec, spec.path(key), MAX_RECORD_NODES - len(nodes))
    relative = owner.relative + (field.name.encode("ascii"), encode_key(child_key))
    new = tuple(walk_values(field.node.fields, validated, relative))
    replace_secondary(tx, spec, spec.path(key), (), new)
    for node, value in nodes.items():
        tx.store.set(node, value)
    tx.store.set(path, b"c:" + str(count + 1).encode("ascii"))


def delete(tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath) -> None:
    if type(at) is not tuple or len(at) < 2:
        raise MutationError("Deletion requires a collection and parent-scoped child key")
    owner, field, path, count = _collection(tx, spec, key, at[:-1])
    assert field.node is not None and field.node.key_field is not None
    child_key = at[-1]
    if type(child_key) is not field.node.model.model_fields[field.node.key_field].annotation:
        raise MutationError("Child identity type does not match its declaration")
    child = path.child(encode_key(child_key))
    require_object(tx.store, child)
    values = node_values(field.node.fields, child, tx.store)
    record = field.node.model.model_validate(values)
    if getattr(record, field.node.key_field) != child_key:
        raise StorageError("Stored child identity does not match its path")
    _length(owner, field, count - 1)
    relative = owner.relative + (field.name.encode("ascii"), encode_key(child_key))
    old = tuple(walk_values(field.node.fields, record, relative))
    replace_secondary(tx, spec, spec.path(key), old, ())
    tx.store.delete_tree(child)
    tx.store.set(path, b"c:" + str(count - 1).encode("ascii"))
