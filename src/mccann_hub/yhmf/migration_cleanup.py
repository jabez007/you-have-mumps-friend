# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded leaf removal for index namespaces retired by a reviewed migration."""

from dataclasses import replace

from .errors import StorageError
from .maintenance import MigrationStatus, job_path, save_status
from .migrations import MigrationPlan
from .registry import ModelSpec
from .storage import Path, Store


def retired_roots(spec: ModelSpec, plan: MigrationPlan) -> tuple[Path, ...]:
    roots = set()
    for change in plan.changes:
        if change.kind == "index_removed":
            globals_to_remove: tuple[str, ...] = ("^YHMFIndex", "^YHMFUnique")
        elif change.kind == "index_changed" and not change.unique:
            globals_to_remove = ("^YHMFUnique",)
        else:
            continue
        for name in globals_to_remove:
            roots.add(
                Path(
                    name,
                    (spec.global_name[1:].encode("ascii"), ".".join(change.path).encode("ascii")),
                )
            )
    return tuple(sorted(roots, key=lambda root: (root.global_name, root.subscripts)))


def removed_count(store: Store, migration_id: str) -> int:
    root = job_path(migration_id).child(b"cleaned_nodes")
    raw = store.get(root)
    if store.children(root, limit=1):
        raise StorageError("Migration cleanup counter contains an unexpected subtree")
    if raw is None:
        return 0
    try:
        count = int(raw)
    except ValueError as exc:
        raise StorageError("Invalid migration cleanup counter") from exc
    if count < 0 or str(count).encode("ascii") != raw:
        raise StorageError("Invalid migration cleanup counter")
    return count


def empty(store: Store, roots: tuple[Path, ...]) -> bool:
    return all(store.get(root) is None and not store.children(root, limit=1) for root in roots)


def _first_leaf(store: Store, root: Path) -> Path | None:
    path = root
    while children := store.children(path, limit=1):
        path = path.child(children[0])
    return path if store.get(path) is not None else None


def advance(
    store: Store, status: MigrationStatus, roots: tuple[Path, ...], *, limit: int
) -> MigrationStatus:
    """Delete at most limit leaves; their absence is the durable traversal progress."""
    count = removed_count(store, status.migration_id)
    deleted = 0
    for root in roots:
        while deleted < limit:
            leaf = _first_leaf(store, root)
            if leaf is None:
                break
            store.delete_tree(leaf)
            deleted += 1
        if deleted == limit:
            break
    if deleted:
        store.set(
            job_path(status.migration_id).child(b"cleaned_nodes"),
            str(count + deleted).encode("ascii"),
        )
        result = status
    else:
        result = replace(status, phase="activate", cursor=b"")
    save_status(store, result)
    return result
