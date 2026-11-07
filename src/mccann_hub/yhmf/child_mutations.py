# SPDX-License-Identifier: GPL-3.0-or-later
"""Direct keyed ownership changes using the existing codecs and secondary entries."""

from typing import TYPE_CHECKING

from . import rollups, secondary
from .codecs import Key, encode_key
from .errors import AlreadyExists, MutationError, StorageError
from .models import Model
from .owned import (
    OwnedPath,
    check_collection_length,
    require_object,
    resolve_collection,
)
from .record_budget import require_capacity
from .records import (
    MAX_RECORD_NODES,
    input_values,
    node_nodes,
    node_values,
    validate_stored,
    walk_values,
)
from .registry import ModelSpec

if TYPE_CHECKING:
    from .repository import Database


def insert(tx: "Database", spec: ModelSpec, key: Key, collection: OwnedPath, record: Model) -> None:
    owner, field, path, count = resolve_collection(tx, spec, key, collection, mutate=True)
    assert field.node is not None and field.node.key_field is not None
    if not isinstance(record, field.node.model):
        raise MutationError("Inserted child type does not match its declared model")
    validated = field.node.model.model_validate(input_values(field.node.fields, record))
    validated = rollups.initialize(field.node.fields, validated)
    child_key = getattr(validated, field.node.key_field)
    if type(child_key) is not field.node.model.model_fields[field.node.key_field].annotation:
        raise MutationError("Child identity type does not match its declaration")
    child = path.child(encode_key(child_key))
    if tx.store.get(child) is not None or tx.store.children(child, limit=1):
        raise AlreadyExists("Child insertion cannot replace existing data")
    check_collection_length(owner, field, count + 1)
    nodes = {child: b"o:1"} | node_nodes(field.node.fields, child, validated)
    require_capacity(tx.store, spec, spec.path(key), MAX_RECORD_NODES - len(nodes))
    relative = owner.relative + (field.name.encode("ascii"), encode_key(child_key))
    new = tuple(walk_values(field.node.fields, validated, relative))
    secondary.replace(tx, (secondary.Change(spec, spec.path(key), (), new),))
    for node, value in nodes.items():
        tx.store.set(node, value)
    tx.store.set(path, b"c:" + str(count + 1).encode("ascii"))


def delete(tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath) -> None:
    if type(at) is not tuple or len(at) < 2:
        raise MutationError("Deletion requires a collection and parent-scoped child key")
    owner, field, path, count = resolve_collection(tx, spec, key, at[:-1], mutate=True)
    assert field.node is not None and field.node.key_field is not None
    child_key = at[-1]
    if type(child_key) is not field.node.model.model_fields[field.node.key_field].annotation:
        raise MutationError("Child identity type does not match its declaration")
    child = path.child(encode_key(child_key))
    require_object(tx.store, child)
    values = node_values(field.node.fields, child, tx.store)
    record = validate_stored(field.node.fields, field.node.model, values)
    if getattr(record, field.node.key_field) != child_key:
        raise StorageError("Stored child identity does not match its path")
    check_collection_length(owner, field, count - 1)
    relative = owner.relative + (field.name.encode("ascii"), encode_key(child_key))
    old = tuple(walk_values(field.node.fields, record, relative))
    secondary.replace(tx, (secondary.Change(spec, spec.path(key), old, ()),))
    tx.store.delete_tree(child)
    tx.store.set(path, b"c:" + str(count - 1).encode("ascii"))
