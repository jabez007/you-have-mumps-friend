# SPDX-License-Identifier: GPL-3.0-or-later
"""Bound growing mutations using declared hierarchical value counts."""

from collections.abc import Iterable

from .errors import StorageError
from .owned import collection_count, require_object
from .records import MAX_CHILDREN, MAX_RECORD_NODES
from .registry import FieldSpec, ModelSpec
from .scalar_lists import length
from .storage import Path, Store


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
    if all(nested.node is None and nested.item is None for nested in field.node.fields):
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


def require_capacity(store: Store, spec: ModelSpec, root: Path, allowance: int) -> None:
    # Fixed-width children use c:N without reading siblings. Only variable-width
    # owned branches need traversal. This is a budget check, not a raw-data audit.
    count = 4  # Existing layout-one root metadata, checked before this traversal.
    pending = [(field, root.child(field.name.encode("ascii"))) for field in spec.fields]
    while pending:
        field, path = pending.pop()
        count += 1
        if count + len(pending) > allowance:
            raise StorageError("Mutation exceeds the root record node budget")
        if field.item is not None:
            marker = store.get(path)
            count += 0 if marker == b"n:" else length(marker)
        if field.node is not None:
            children, markers = _owned_nodes(store, field, path)
            count += markers
            pending.extend(
                (nested, child.child(nested.name.encode("ascii")))
                for child in children
                for nested in field.node.fields
            )
    if count > allowance:
        raise StorageError("Mutation exceeds the root record node budget")


def require_growth(store: Store, changes: Iterable[tuple[ModelSpec, Path, int]]) -> None:
    """Check the final net growth once per root before applying grouped changes."""
    grouped: dict[Path, tuple[ModelSpec, int]] = {}
    for spec, root, delta in changes:
        _, growth = grouped.get(root, (spec, 0))
        grouped[root] = spec, growth + delta
    for root, (spec, growth) in grouped.items():
        if growth > 0:
            require_capacity(store, spec, root, MAX_RECORD_NODES - growth)
