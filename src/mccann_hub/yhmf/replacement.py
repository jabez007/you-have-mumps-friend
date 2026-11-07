# SPDX-License-Identifier: GPL-3.0-or-later
"""Whole-value replacement with bounded physical auditing and minimal node writes."""

from .records import MAX_RECORD_NODES
from .storage import Path, Store, subtree_nodes


def _under(subscripts: tuple[bytes, ...], roots: set[tuple[bytes, ...]]) -> bool:
    return any(subscripts[:length] in roots for length in range(len(subscripts) + 1))


def apply(store: Store, root: Path, desired: dict[Path, bytes]) -> None:
    # All encoded value nodes are budgeted by records. The record root and %m
    # branch are the only two additional valueless addresses in a valid record.
    actual = subtree_nodes(store, root, limit=MAX_RECORD_NODES + 2)
    removed = actual.keys() - desired.keys()
    deleted: set[tuple[bytes, ...]] = set()
    for path in sorted(removed, key=lambda path: (len(path.subscripts), path.subscripts)):
        if not _under(path.subscripts, deleted):
            store.delete_tree(path)
            deleted.add(path.subscripts)
    for path, value in desired.items():
        # KILL removes descendants too. Retain replacement semantics even when
        # raw data put an extra value on a normally valueless ancestor.
        if actual.get(path) != value or _under(path.subscripts, deleted):
            store.set(path, value)
