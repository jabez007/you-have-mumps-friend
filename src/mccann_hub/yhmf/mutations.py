# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional scalar changes at explicit ownership paths."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import indexes, references
from .codecs import Key, encode_key
from .errors import MutationError, StorageError
from .models import Model
from .owned import OwnedPath, Owner, require_local_validation, resolve
from .record_budget import require_capacity
from .records import MAX_RECORD_NODES, FieldValue, node_values, value_nodes
from .registry import FieldSpec, ModelSpec
from .storage import Path, Store

if TYPE_CHECKING:
    from .repository import Database


def read(
    tx: "Database",
    spec: ModelSpec,
    key: Key,
    at: OwnedPath,
    *,
    expected: type[Model] | None = None,
) -> Model:
    owner = resolve(tx, spec, key, at)
    if expected is not None and owner.node.model is not expected:
        raise MutationError("Typed path target differs from its owned declaration")
    return read_owner(tx.store, owner)


def read_owner(store: Store, owner: Owner) -> Model:
    """Validate a resolved object's values and identity without rereading its ancestors."""
    values = node_values(owner.node.fields, owner.path, store)
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
        raise MutationError("Patch requires declared scalar, scalar list, or Reference fields")
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
    require_local_validation(owner.node)
    old = node_values(selected, owner.path, tx.store)
    new = {}
    for field in selected:
        adapter = field.adapter
        assert adapter is not None
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


@dataclass(frozen=True)
class PreparedPatch:
    spec: ModelSpec
    root: Path
    owner: Path
    before: tuple[FieldValue, ...]
    after: tuple[FieldValue, ...]
    writes: tuple[tuple[Path, bytes], ...]
    deletes: tuple[Path, ...]
    growth: int

    def apply_nodes(self, store: Store) -> None:
        for path in self.deletes:
            store.delete_tree(path)
        for path, value in self.writes:
            store.set(path, value)


def prepare_patch(
    tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath, changes: Mapping[str, Any]
) -> PreparedPatch:
    """Validate one owner and prepare its exact delta without persisting it."""
    owner = resolve(tx, spec, key, at, mutate=True)
    selected = _fields(owner, changes)
    old, new, fields = _validated(tx, owner, selected, changes)
    before_nodes = {
        field.name: value_nodes(
            field, owner.path.child(field.name.encode("ascii")), old[field.name]
        )
        for field in fields
    }
    after_nodes = {
        field.name: value_nodes(
            field, owner.path.child(field.name.encode("ascii")), new[field.name]
        )
        for field in fields
    }
    changed = tuple(
        field for field in fields if before_nodes[field.name] != after_nodes[field.name]
    )
    before, after = _occurrences(owner, changed, old), _occurrences(owner, changed, new)
    old_nodes = {
        path: value for field in changed for path, value in before_nodes[field.name].items()
    }
    new_nodes = {
        path: value for field in changed for path, value in after_nodes[field.name].items()
    }
    writes = tuple(
        (path, value) for path, value in new_nodes.items() if old_nodes.get(path) != value
    )
    deletes = tuple(sorted(old_nodes.keys() - new_nodes.keys(), key=lambda path: path.subscripts))
    return PreparedPatch(
        spec,
        spec.path(key),
        owner.path,
        before,
        after,
        writes,
        deletes,
        len(new_nodes) - len(old_nodes),
    )


def check_growth(tx: "Database", prepared: tuple[PreparedPatch, ...]) -> None:
    grouped: dict[Path, tuple[ModelSpec, int]] = {}
    for item in prepared:
        _, growth = grouped.get(item.root, (item.spec, 0))
        grouped[item.root] = item.spec, growth + item.growth
    for root, (spec, growth) in grouped.items():
        if growth > 0:
            require_capacity(tx.store, spec, root, MAX_RECORD_NODES - growth)


def patch(
    tx: "Database", spec: ModelSpec, key: Key, at: OwnedPath, changes: Mapping[str, Any]
) -> None:
    prepared = prepare_patch(tx, spec, key, at, changes)
    check_growth(tx, (prepared,))
    replace_secondary(tx, spec, prepared.root, prepared.before, prepared.after)
    prepared.apply_nodes(tx.store)
