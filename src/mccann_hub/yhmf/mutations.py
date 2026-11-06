# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional scalar changes at explicit ownership paths."""

from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from . import indexes, references
from .codecs import Key, encode_key
from .errors import MutationError, StorageError
from .models import Model
from .owned import OwnedPath, Owner, require_local_validation, resolve
from .records import FieldValue, encode_leaf, node_values
from .registry import FieldSpec, ModelSpec, field_adapter
from .storage import Path

if TYPE_CHECKING:
    from .repository import Database


def read(tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath) -> Model:
    owner = resolve(tx, spec, key, at)
    values = node_values(owner.node.fields, owner.path, tx.store)
    result = owner.node.model.model_validate(values)
    _identity(
        owner, values, {field.name: getattr(result, field.name) for field in owner.node.fields}
    )
    return result


def _identity(owner: Owner, old: Mapping[str, Any], new: Mapping[str, Any]) -> None:
    key = owner.node.key_field
    if key is not None and old[key] != new[key]:
        raise MutationError("Partial mutation cannot change record identity")
    if key is not None and owner.path.subscripts[-1] != encode_key(new[key]):
        raise StorageError("Owned record identity does not match its path")


def _fields(owner: Owner, changes: Mapping[str, Any]) -> tuple[FieldSpec, ...]:
    by_name = {field.name: field for field in owner.node.fields}
    if not changes or changes.keys() - by_name.keys():
        raise MutationError("Patch requires one or more declared scalar or Reference fields")
    if owner.node.key_field in changes:
        raise MutationError("Partial mutation cannot change record identity")
    selected = tuple(by_name[name] for name in changes)
    if any(field.node is not None for field in selected):
        raise MutationError("Patch scalar fields; address owned objects using at")
    return selected


def _validated(
    tx: "Database", owner: Owner, selected: tuple[FieldSpec, ...], changes: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], tuple[FieldSpec, ...]]:
    if all(field.node is None for field in owner.node.fields):
        # Full leaf validation retains arbitrary cross-field and model validators.
        fields = owner.node.fields
        old = node_values(fields, owner.path, tx.store)
        previous = owner.node.model.model_validate(deepcopy(old))
        old = {field.name: getattr(previous, field.name) for field in fields}
        result = owner.node.model.model_validate(deepcopy(old) | deepcopy(dict(changes)))
        new = {field.name: getattr(result, field.name) for field in fields}
        _identity(owner, old, new)
        return old, new, fields
    require_local_validation(owner.node.model)
    old = node_values(selected, owner.path, tx.store)
    new = {}
    for field in selected:
        adapter = field_adapter(owner.node.model, field.name)
        old[field.name] = adapter.validate_python(old[field.name])
        new[field.name] = adapter.validate_python(deepcopy(changes[field.name]))
    return old, new, selected


def _occurrences(
    owner: Owner, fields: tuple[FieldSpec, ...], values: Mapping[str, Any]
) -> tuple[FieldValue, ...]:
    return tuple(
        (field, owner.relative + (field.name.encode("ascii"),), values[field.name])
        for field in fields
    )


def replace_secondary(
    tx: "Database",
    spec: ModelSpec,
    root: Path,
    old: tuple[FieldValue, ...],
    new: tuple[FieldValue, ...],
) -> None:
    old_links = references.links_at(tx.registry, spec, root, old)
    new_links = references.links_at(tx.registry, spec, root, new)
    tx._check_reference_targets(new_links)
    references.replace(tx.store, old_links, new_links)
    indexes.replace(
        tx.store, indexes.entries_at(spec, root, old), indexes.entries_at(spec, root, new)
    )


def patch(
    tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath, changes: Mapping[str, Any]
) -> None:
    owner = resolve(tx, spec, key, at, mutate=True)
    selected = _fields(owner, changes)
    old, new, fields = _validated(tx, owner, selected, changes)
    changed = tuple(
        field
        for field in fields
        if encode_leaf(field, old[field.name]) != encode_leaf(field, new[field.name])
    )
    before, after = _occurrences(owner, changed, old), _occurrences(owner, changed, new)
    replace_secondary(tx, spec, spec.path(key), before, after)
    for field in changed:
        tx.store.set(
            owner.path.child(field.name.encode("ascii")), encode_leaf(field, new[field.name])
        )
