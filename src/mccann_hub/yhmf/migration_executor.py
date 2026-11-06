# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded scalar and index migrations behind an owned maintenance gate."""

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from . import indexes, migration_claims, migration_cleanup, migration_jobs, references
from .codecs import decode_key
from .errors import MigrationError, StorageError
from .maintenance import (
    MigrationStatus,
    load_status,
    require_owner,
    save_status,
)
from .migration_defaults import BackfillDefaults
from .migration_records import check_metadata, field_nodes, validate_targets
from .migrations import MigrationPlan, build_plan
from .models import Model
from .records import metadata, record_values
from .registry import ModelSpec, Registry
from .schemas import SchemaInspection, SchemaSnapshot
from .storage import Path, Store

MAX_BATCH_SIZE = 100
_COUNTER = {
    "validate": "validated",
    "backfill": "backfilled",
    "verify": "verified",
    "retag": "retagged",
}
_NEXT = {"validate": "backfill", "backfill": "verify", "verify": "retag", "retag": "activate"}


@dataclass(frozen=True)
class _Binding:
    source: ModelSpec
    target: ModelSpec
    registry: Registry
    plan: MigrationPlan
    defaults: BackfillDefaults
    cleanup_roots: tuple[Path, ...]


def _bind(registry: Registry, source: type[Model], target: type[Model]) -> _Binding:
    old = Registry().register(source)
    new = Registry().register(target)
    if registry.spec(target).snapshot != new.snapshot:
        raise MigrationError("Target declaration changed after registration")
    inspection = SchemaInspection(SchemaSnapshot(new.snapshot), SchemaSnapshot(old.snapshot), True)
    plan = build_plan(inspection)
    if not plan.execution_supported:
        raise MigrationError("Migration supports scalar defaults and scalar index changes")
    defaults = BackfillDefaults(new, plan)
    return _Binding(old, new, registry, plan, defaults, migration_cleanup.retired_roots(old, plan))


def _check_review(binding: _Binding, reviewed: MigrationPlan) -> None:
    source = reviewed.inspection.installed
    if source is None or source.canonical != binding.source.snapshot:
        raise MigrationError("Reviewed source does not match the supplied source model")
    if reviewed.inspection.declared.canonical != binding.target.snapshot:
        raise MigrationError("Reviewed target does not match the supplied target model")
    if reviewed.changes != binding.plan.changes or reviewed.steps != binding.plan.steps:
        raise MigrationError("Reviewed plan differs from the canonical schema comparison")


def _check_new_indexes_empty(store: Store, binding: _Binding) -> None:
    for change in binding.plan.changes:
        if change.kind not in {"index_added", "index_changed"}:
            continue
        if change.kind == "index_changed" and not change.unique:
            continue
        path = Path(
            "^YHMFIndex",
            (binding.target.global_name[1:].encode("ascii"), ".".join(change.path).encode("ascii")),
        )
        globals_to_check = (
            ("^YHMFIndex", "^YHMFUnique") if change.kind == "index_added" else ("^YHMFUnique",)
        )
        for global_name in globals_to_check:
            root = Path(global_name, path.subscripts)
            if store.get(root) is not None or store.children(root, limit=1):
                raise StorageError("A newly declared index already contains unmanaged entries")


def begin(
    store: Store,
    registry: Registry,
    source: type[Model],
    target: type[Model],
    reviewed: MigrationPlan,
) -> MigrationStatus:
    binding = _bind(registry, source, target)
    _check_review(binding, reviewed)
    return migration_jobs.begin(
        store,
        binding.source,
        binding.target,
        preflight=lambda: _check_new_indexes_empty(store, binding),
    )


def _validate_models(binding: _Binding, values: dict[str, Any]) -> tuple[Model, Model]:
    try:
        old = binding.source.model.model_validate(deepcopy(values))
        new = binding.target.model.model_validate(binding.defaults.apply(values))
    except ValidationError:
        raise MigrationError(
            "A stored record does not satisfy the migration model bindings"
        ) from None
    return old, new


@dataclass(frozen=True)
class _Record:
    root: Path
    additions: dict[Path, bytes]
    old_entries: frozenset[indexes.Entry]
    new_entries: frozenset[indexes.Entry]


def _prepare(store: Store, binding: _Binding, key: bytes, status: MigrationStatus) -> _Record:
    root = binding.source.path(decode_key(key))
    check_metadata(store, binding.source, root)
    values = record_values(binding.source, root, store)
    old, new = _validate_models(binding, values)
    if binding.target.path(getattr(new, binding.target.key_field)) != root:
        raise MigrationError("Migration validation changed a record identity")
    old_nodes = field_nodes(binding.source, root, old)
    new_nodes = field_nodes(binding.target, root, new)
    if any(
        new_nodes.get(path) != value or store.get(path) != value
        for path, value in old_nodes.items()
    ):
        raise MigrationError("Migration validation would change an existing field")
    additions = {path: value for path, value in new_nodes.items() if path not in old_nodes}
    _check_additions(store, additions, status.phase)
    old_entries = indexes.entries(binding.source, root, old)
    new_entries = indexes.entries(binding.target, root, new)
    transition = indexes.check_transition(
        store, old_entries, new_entries, updated=status.phase in {"verify", "retag"}
    )
    validate_targets(
        store,
        binding.registry,
        binding.source,
        binding.target,
        references.links(binding.registry, binding.target, root, new),
    )
    if status.phase == "validate":
        migration_claims.reserve(store, status.migration_id, transition.claims)
    else:
        migration_claims.verify(store, status.migration_id, transition.claims)
    return _Record(root, additions, old_entries, new_entries)


def _check_additions(store: Store, additions: dict[Path, bytes], phase: str) -> None:
    for path, value in additions.items():
        expected = value if phase in {"verify", "retag"} else None
        if store.get(path) != expected or store.children(path, limit=1):
            raise StorageError("A backfill field is missing, corrupt, or already occupied")


def _process(store: Store, binding: _Binding, key: bytes, status: MigrationStatus) -> None:
    record = _prepare(store, binding, key, status)
    if status.phase == "backfill":
        for path, value in record.additions.items():
            store.set(path, value)
        indexes.apply_transition(store, record.old_entries, record.new_entries)
    elif status.phase == "retag":
        for name, value in metadata(binding.target).items():
            store.set(record.root.child(b"%m", name), value)


def _check_counts(status: MigrationStatus) -> None:
    counts = (status.validated, status.backfilled, status.verified, status.retagged)
    index = ("validate", "backfill", "verify", "retag", "cleanup", "activate", "complete").index(
        status.phase
    )
    if len(set(counts[:index])) > 1 or any(counts[index + 1 :]):
        raise StorageError("Migration counters do not match the current phase")


def _activate(
    store: Store, status: MigrationStatus, target: bytes, retired: tuple[Path, ...]
) -> MigrationStatus:
    if not migration_cleanup.empty(store, retired):
        result = replace(status, phase="cleanup", cursor=b"")
        save_status(store, result)
        return result
    return migration_jobs.complete(store, status, target)


def advance(
    store: Store,
    registry: Registry,
    migration_id: str,
    source: type[Model],
    target: type[Model],
    *,
    batch_size: int,
) -> MigrationStatus:
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise MigrationError("Migration batch size must be between 1 and 100")
    binding = _bind(registry, source, target)

    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        if status.phase == "complete":
            return _completed_binding(status, binding)
        if status.phase == "aborted":
            raise MigrationError("Migration was aborted")
        require_owner(store, status)
        manifests = migration_jobs.manifests(store, status)
        if manifests != (binding.source.snapshot, binding.target.snapshot):
            raise MigrationError("Resume bindings do not match the saved migration snapshots")
        _check_counts(status)
        if status.phase == "activate":
            return _activate(store, status, manifests[1], binding.cleanup_roots)
        if status.phase == "cleanup":
            return migration_cleanup.advance(store, status, binding.cleanup_roots, limit=batch_size)
        return _batch(store, binding, status, batch_size)

    return store.atomic(attempt)


def _completed_binding(status: MigrationStatus, binding: _Binding) -> MigrationStatus:
    if (status.source_fingerprint, status.target_fingerprint) != (
        binding.source.fingerprint.decode("ascii"),
        binding.target.fingerprint.decode("ascii"),
    ):
        raise MigrationError("Bindings do not match the completed migration")
    return status


def _batch(store: Store, binding: _Binding, status: MigrationStatus, limit: int) -> MigrationStatus:
    root = Path(status.global_name)
    if store.get(root) is not None:
        raise StorageError("An unmanaged value occupies the application global root")
    keys = store.children(root, limit=limit, after=status.cursor)
    for key in keys:
        _process(store, binding, key, status)
    counter = _COUNTER[status.phase]
    changes: dict[str, Any] = {counter: getattr(status, counter) + len(keys)}
    if keys:
        changes["cursor"] = keys[-1]
    else:
        changes.update(phase=_NEXT[status.phase], cursor=b"")
        if status.phase == "retag" and binding.cleanup_roots:
            changes["phase"] = "cleanup"
    updated = replace(status, **changes)
    _check_counts(updated)
    save_status(store, updated)
    return updated
