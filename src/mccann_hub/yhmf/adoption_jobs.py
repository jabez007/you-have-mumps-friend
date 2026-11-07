# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded initial adoption using the existing maintenance journal and ownership."""

import hashlib
from dataclasses import replace
from typing import Any

from . import adoption, indexes, migration_claims, migration_jobs, references
from .codecs import canonical_json, decode_key, encode_scalar
from .errors import MigrationError, StorageError
from .maintenance import GATE, MigrationStatus, job_path, load_status, require_owner, save_status
from .migration_records import check_metadata, field_digest, field_nodes, validate_target_metadata
from .models import Model
from .registry import ModelSpec, Registry
from .schemas import SchemaSnapshot, has_data, schema_path
from .storage import Path, Store

_COUNTER = {"validate": "validated", "backfill": "backfilled", "verify": "verified"}
_NEXT = {"validate": "backfill", "backfill": "verify", "verify": "activate"}
_OPERATION = canonical_json({"format_version": 1, "kind": "adoption"})


def _spec(registry: Registry, model: type[Model]) -> ModelSpec:
    spec = registry.spec(model)
    if Registry().register(model).snapshot != spec.snapshot:
        raise MigrationError("Adoption model changed after registration")
    return spec


def begin(store: Store, registry: Registry, model: type[Model]) -> MigrationStatus:
    spec = _spec(registry, model)
    return migration_jobs.begin(
        store,
        None,
        spec,
        preflight=lambda: adoption.require_unmanaged(store, spec),
        operation=_OPERATION,
    )


def _snapshot(store: Store, status: MigrationStatus) -> bytes:
    if status.source_fingerprint is not None:
        raise MigrationError("Job is not an initial adoption")
    operation = migration_jobs.load_operation(store, status)
    if operation != {"format_version": 1, "kind": "adoption"}:
        raise MigrationError("Job is not an initial adoption")
    raw = store.get(job_path(status.migration_id).child(b"target_snapshot"))
    if raw is None:
        raise StorageError("Adoption target snapshot is missing")
    snapshot = SchemaSnapshot(raw)
    if (
        snapshot.global_name != status.global_name
        or snapshot.fingerprint != status.target_fingerprint
    ):
        raise StorageError("Adoption target snapshot differs from its checkpoint")
    if has_data(store, job_path(status.migration_id).child(b"source_snapshot")):
        raise StorageError("Initial adoption cannot contain a source snapshot")
    return raw


def _active(store: Store, status: MigrationStatus) -> None:
    require_owner(store, status)
    if store.children(schema_path(status.global_name), limit=1):
        raise StorageError("Adoption published unexpected installed schema metadata")


def _counts(status: MigrationStatus) -> None:
    if status.phase not in {*_COUNTER, "activate", "complete"} or status.retagged:
        raise StorageError("Invalid adoption phase or metadata counter")
    if status.phase == "validate" and (status.backfilled or status.verified):
        raise StorageError("Adoption updates precede validation")
    if status.phase == "backfill" and status.verified:
        raise StorageError("Adoption verification precedes backfill")
    if status.phase == "verify" and status.validated != status.backfilled:
        raise StorageError("Adoption record counts disagree")
    if (
        status.phase in {"activate", "complete"}
        and len({status.validated, status.backfilled, status.verified}) != 1
    ):
        raise StorageError("Adoption record counts disagree")


def _targets(
    store: Store, registry: Registry, spec: ModelSpec, links: frozenset[references.Link]
) -> None:
    external = frozenset(link for link in links if link.target.global_name != spec.global_name)
    validate_target_metadata(store, registry, spec, spec, external)
    for link in links - external:
        # Every record is audited in the validation pass. Check self-target identity
        # directly rather than decoding the same aggregate for every incoming edge.
        key = decode_key(link.target.subscripts[0])
        if store.get(link.target) is not None or store.get(
            link.target.child(spec.key_field.encode())
        ) != encode_scalar(key):
            raise StorageError(
                "Adoption self-reference target is missing or has the wrong identity"
            )
        if has_data(store, link.target.child(b"%m")):
            check_metadata(store, spec, link.target)


def _pin(store: Store, status: MigrationStatus, root: Path, nodes: dict[Path, bytes]) -> None:
    key = root.subscripts[0]
    path = job_path(status.migration_id).child(b"records", hashlib.sha256(key).hexdigest().encode())
    expected = canonical_json({"key": key.hex(), "fields": field_digest(root, nodes)})
    current = store.get(path)
    if status.phase == "validate" and current is None:
        store.set(path, expected)
    elif current != expected:
        raise StorageError("Adoption record changed since validation or its digest is corrupt")


def _process(
    store: Store, registry: Registry, spec: ModelSpec, status: MigrationStatus, key: bytes
) -> None:
    root, record, _ = adoption.read_record(store, spec, key)
    _pin(store, status, root, field_nodes(spec, root, record))
    entries = indexes.entries(spec, root, record)
    claims = frozenset(entry for entry in entries if entry.claim_path is not None)
    links = references.links(registry, spec, root, record)
    _targets(store, registry, spec, links)
    if status.phase == "validate":
        migration_claims.reserve(store, status.migration_id, claims)
        return
    migration_claims.verify(store, status.migration_id, claims)
    if status.phase == "backfill":
        references.replace(store, frozenset(), links)
        indexes.apply_transition(store, frozenset(), entries)
        adoption.install_record_metadata(store, spec, root)
    else:
        check_metadata(store, spec, root)
        references.replace(store, links, links)
        indexes.check_transition(store, frozenset(), entries, updated=True)


def _batch(
    store: Store, registry: Registry, spec: ModelSpec, status: MigrationStatus, limit: int
) -> MigrationStatus:
    root = Path(spec.global_name)
    if store.get(root) is not None:
        raise StorageError("An unmanaged value occupies the application global root")
    keys = store.children(root, limit=limit, after=status.cursor)
    for key in keys:
        _process(store, registry, spec, status, key)
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
    store: Store, registry: Registry, migration_id: str, model: type[Model], *, batch_size: int
) -> MigrationStatus:
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise MigrationError("Adoption batch size must be between 1 and 100")
    spec = _spec(registry, model)

    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        if _snapshot(store, status) != spec.snapshot:
            raise MigrationError("Adoption declaration differs from the saved target")
        if status.phase == "aborted":
            raise MigrationError("Adoption was aborted")
        _counts(status)
        if status.phase == "complete":
            return status
        _active(store, status)
        if status.phase == "activate":
            return migration_jobs.complete(store, status, spec.snapshot)
        return _batch(store, registry, spec, status, batch_size)

    return store.atomic(attempt)


def abort_owned(store: Store, status: MigrationStatus) -> MigrationStatus:
    """Restore absent metadata before any committed backfill, inside shared abort TP."""
    _snapshot(store, status)
    _counts(status)
    _active(store, status)
    store.delete_tree(schema_path(status.global_name))
    result = replace(status, phase="aborted", cursor=b"")
    save_status(store, result)
    store.delete_tree(GATE)
    return result
