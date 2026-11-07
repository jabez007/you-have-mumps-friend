# SPDX-License-Identifier: GPL-3.0-or-later
"""Record transforms within the existing maintenance lifecycle and storage format."""

from dataclasses import replace
from typing import Any

from . import migration_cleanup, migration_jobs, transform_records
from .codecs import canonical_json
from .errors import MigrationError, StorageError
from .maintenance import MigrationStatus, load_status, require_owner, save_status
from .migrations import MigrationPlan, build_plan
from .models import Model
from .registry import Registry
from .schemas import SchemaInspection, SchemaSnapshot
from .storage import Path, Store
from .transforms import Transform

_COUNTER = {
    "validate": "validated",
    "backfill": "backfilled",
    "verify": "verified",
    "retag": "retagged",
}
_NEXT = {"validate": "clear_indexes", "backfill": "verify", "verify": "retag", "retag": "activate"}


def _operation(transform: Transform[Any, Any]) -> dict[str, Any]:
    return {
        "format_version": 1,
        "kind": "record_transform",
        "name": transform.name,
        "digest_version": 1,
    }


def _plan(binding: transform_records.Binding) -> MigrationPlan:
    inspection = SchemaInspection(
        SchemaSnapshot(binding.target.snapshot), SchemaSnapshot(binding.source.snapshot), True
    )
    return build_plan(inspection, transform_id=binding.transform.name)


def begin(
    store: Store,
    registry: Registry,
    source: type[Model],
    target: type[Model],
    reviewed: MigrationPlan,
    transform: Transform[Any, Any],
) -> MigrationStatus:
    installed = reviewed.inspection.installed
    if (
        installed is None
        or reviewed.inspection.declared.canonical != registry.spec(target).snapshot
    ):
        raise MigrationError("Transform requires the reviewed source and target schemas")
    binding = transform_records.bind(
        registry, source, target, transform, installed, reviewed.inspection.declared
    )
    actual = _plan(binding)
    if (
        not actual.execution_supported
        or reviewed.transform_id != transform.name
        or reviewed.changes != actual.changes
        or reviewed.steps != actual.steps
    ):
        raise MigrationError("Reviewed transform plan differs from its declarations or revision")
    return migration_jobs.begin(
        store, binding.source, binding.target, operation=canonical_json(_operation(transform))
    )


def _counts(status: MigrationStatus) -> None:
    positions = {
        "validate": 0,
        "clear_indexes": 1,
        "backfill": 1,
        "verify": 2,
        "retag": 3,
        "activate": 4,
        "complete": 4,
    }
    if status.phase not in positions:
        raise StorageError("Invalid transform phase")
    count = positions[status.phase]
    values = (status.validated, status.backfilled, status.verified, status.retagged)
    if (
        len(set(values[:count])) > 1
        or any(values[count + 1 :])
        or (status.phase == "clear_indexes" and status.backfilled)
    ):
        raise StorageError("Transform counters do not match their phase")


def _roots(binding: transform_records.Binding) -> tuple[Path, ...]:
    return tuple(
        Path(name, (binding.source.global_name[1:].encode("ascii"),))
        for name in ("^YHMFIndex", "^YHMFUnique")
    )


def _batch(
    store: Store, binding: transform_records.Binding, status: MigrationStatus, limit: int
) -> MigrationStatus:
    root = Path(status.global_name)
    if store.get(root) is not None:
        raise StorageError("An unmanaged value occupies the application global root")
    keys = store.children(root, limit=limit, after=status.cursor)
    for key in keys:
        transform_records.process(store, binding, status, key)
    counter = _COUNTER[status.phase]
    changes: dict[str, Any] = {counter: getattr(status, counter) + len(keys)}
    if keys:
        changes["cursor"] = keys[-1]
    else:
        changes.update(phase=_NEXT[status.phase], cursor=b"")
    result = replace(status, **changes)
    _counts(result)
    save_status(store, result)
    return result


def advance(
    store: Store,
    registry: Registry,
    migration_id: str,
    source: type[Model],
    target: type[Model],
    transform: Transform[Any, Any],
    *,
    batch_size: int,
) -> MigrationStatus:
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise MigrationError("Transform batch size must be between 1 and 100")

    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        if migration_jobs.load_operation(store, status) != _operation(transform):
            raise MigrationError("Job requires its exact reviewed transform revision")
        saved = migration_jobs.saved_snapshots(store, status)
        binding = transform_records.bind(
            registry, source, target, transform, *(SchemaSnapshot(raw) for raw in saved)
        )
        if not _plan(binding).execution_supported:
            raise MigrationError("Saved transform schema change is unsupported")
        if status.phase == "aborted":
            raise MigrationError("Transform was aborted")
        _counts(status)
        if status.phase == "complete":
            return status
        require_owner(store, status)
        migration_jobs.manifests(store, status)
        if status.phase == "activate":
            return migration_jobs.complete(store, status, binding.target.snapshot)
        if status.phase == "clear_indexes":
            deleted = migration_cleanup.clear_batch(
                store, migration_id, _roots(binding), limit=batch_size
            )
            result = status if deleted else replace(status, phase="backfill", cursor=b"")
            save_status(store, result)
            return result
        return _batch(store, binding, status, batch_size)

    return store.atomic(attempt)
