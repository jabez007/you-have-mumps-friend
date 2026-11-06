# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared ownership, snapshots, activation, and abort for maintenance jobs."""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import replace

from . import migration_cleanup
from .errors import MigrationError, StorageError
from .maintenance import (
    GATE,
    MigrationStatus,
    job_path,
    load_status,
    marker,
    require_available,
    require_owner,
    save_status,
)
from .registry import ModelSpec
from .schemas import SchemaSnapshot, inspect_schema, schema_path
from .storage import MAX_VALUE_BYTES, Store


def begin(
    store: Store,
    source: ModelSpec,
    target: ModelSpec,
    *,
    preflight: Callable[[], None] | None = None,
    operation: bytes | None = None,
) -> MigrationStatus:
    migration_id = uuid.uuid4().hex  # Stable across native transaction retries.

    def claim() -> MigrationStatus:
        require_available(store)
        if inspect_schema(store, target).installed != SchemaSnapshot(source.snapshot):
            raise MigrationError("Installed source changed since the plan was reviewed")
        if preflight is not None:
            preflight()
        root = job_path(migration_id)
        if store.get(root) is not None or store.children(root, limit=1):
            raise MigrationError("Migration identifier is already in use")
        for name, spec in ((b"source_snapshot", source), (b"target_snapshot", target)):
            if len(spec.snapshot) > MAX_VALUE_BYTES:
                raise StorageError("Migration snapshot exceeds the node limit")
            store.set(root.child(name), spec.snapshot)
        if operation is not None:
            store.set(root.child(b"operation"), operation)
        status = MigrationStatus(
            migration_id,
            target.global_name,
            source.fingerprint.decode("ascii"),
            target.fingerprint.decode("ascii"),
            operation_fingerprint=hashlib.sha256(operation).hexdigest()
            if operation is not None
            else None,
        )
        save_status(store, status)
        store.set(GATE, migration_id.encode("ascii"))
        store.set(schema_path(status.global_name), marker(migration_id))
        return status

    return store.atomic(claim)


def require_schema_operation(store: Store, status: MigrationStatus) -> None:
    if (
        status.operation_fingerprint is not None
        or store.get(job_path(status.migration_id).child(b"operation")) is not None
    ):
        raise MigrationError("This job requires its own maintenance executor")


def saved_snapshots(store: Store, status: MigrationStatus) -> tuple[bytes, bytes]:
    root = job_path(status.migration_id)
    source, target = (
        store.get(root.child(b"source_snapshot")),
        store.get(root.child(b"target_snapshot")),
    )
    if source is None or target is None:
        raise StorageError("Migration snapshots are incomplete")
    for data, fingerprint in (
        (source, status.source_fingerprint),
        (target, status.target_fingerprint),
    ):
        snapshot = SchemaSnapshot(data)
        if snapshot.global_name != status.global_name or snapshot.fingerprint != fingerprint:
            raise StorageError("Migration snapshot does not match its checkpoint")
    return source, target


def manifests(store: Store, status: MigrationStatus) -> tuple[bytes, bytes]:
    source, target = saved_snapshots(store, status)
    if store.get(schema_path(status.global_name).child(b"snapshot")) != source:
        raise StorageError("Installed source snapshot changed during migration")
    return source, target


def complete(store: Store, status: MigrationStatus, target: bytes) -> MigrationStatus:
    """Publish the target and release the gate inside the caller's owned transaction."""
    root = schema_path(status.global_name)
    store.set(root.child(b"snapshot"), target)
    store.set(root, status.target_fingerprint.encode("ascii"))
    completed = replace(status, phase="complete", cursor=b"")
    save_status(store, completed)
    store.delete_tree(GATE)
    return completed


def abort(store: Store, migration_id: str) -> MigrationStatus:
    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        if status.phase == "aborted":
            return status
        if (
            status.phase == "complete"
            or status.backfilled
            or status.retagged
            or migration_cleanup.removed_count(store, migration_id)
        ):
            raise MigrationError(
                "Committed migration updates must be resumed; they cannot be aborted"
            )
        require_owner(store, status)
        source, _ = manifests(store, status)
        store.set(
            schema_path(status.global_name), SchemaSnapshot(source).fingerprint.encode("ascii")
        )
        result = replace(status, phase="aborted", cursor=b"")
        save_status(store, result)
        store.delete_tree(GATE)
        return result

    return store.atomic(attempt)
