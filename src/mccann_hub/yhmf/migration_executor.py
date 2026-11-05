# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, resumable additive migrations behind an owned maintenance gate."""

import uuid
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any

from pydantic import TypeAdapter, ValidationError

from . import indexes, references
from .codecs import decode_key
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
from .migrations import MigrationPlan, build_plan
from .models import Model
from .records import metadata, record_nodes, record_values
from .registry import ModelSpec, Registry
from .schemas import SchemaInspection, SchemaSnapshot, inspect_schema, schema_path
from .storage import MAX_VALUE_BYTES, Path, Store

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
    defaults: dict[str, Any]


def _bind(registry: Registry, source: type[Model], target: type[Model]) -> _Binding:
    old = Registry().register(source)
    new = Registry().register(target)
    if registry.spec(target).snapshot != new.snapshot:
        raise MigrationError("Target declaration changed after registration")
    inspection = SchemaInspection(SchemaSnapshot(new.snapshot), SchemaSnapshot(old.snapshot), True)
    plan = build_plan(inspection)
    if not plan.execution_supported:
        raise MigrationError(
            "Only additive root scalar defaults and non-unique indexes are supported"
        )
    defaults = _literal_defaults(new, plan)
    return _Binding(old, new, registry, plan, defaults)


def _literal_defaults(spec: ModelSpec, plan: MigrationPlan) -> dict[str, Any]:
    result = {}
    for change in plan.changes:
        if change.kind != "field_added":
            continue
        name = change.path[0]
        field = spec.model.model_fields[name]
        if field.default_factory is not None:
            raise MigrationError("Migration defaults must be literals")
        assert change.default_json is not None
        try:
            result[name] = TypeAdapter(field.rebuild_annotation()).validate_json(
                change.default_json, strict=True
            )
        except ValidationError:
            raise MigrationError("A literal default does not satisfy its target field") from None
    return result


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
        if change.kind != "index_added":
            continue
        path = Path(
            "^YHMFIndex",
            (binding.target.global_name[1:].encode("ascii"), change.path[0].encode("ascii")),
        )
        for global_name in ("^YHMFIndex", "^YHMFUnique"):
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
    migration_id = uuid.uuid4().hex  # Stable across native transaction retries.

    def claim() -> MigrationStatus:
        require_available(store)
        inspection = inspect_schema(store, binding.target)
        if inspection.installed != binding.plan.inspection.installed:
            raise MigrationError("Installed source changed since the plan was reviewed")
        _check_new_indexes_empty(store, binding)
        root = job_path(migration_id)
        if store.get(root) is not None or store.children(root, limit=1):
            raise MigrationError("Migration identifier is already in use")
        for name, spec in (
            (b"source_snapshot", binding.source),
            (b"target_snapshot", binding.target),
        ):
            if len(spec.snapshot) > MAX_VALUE_BYTES:
                raise StorageError("Migration snapshot exceeds the node limit")
            store.set(root.child(name), spec.snapshot)
        status = MigrationStatus(
            migration_id,
            binding.target.global_name,
            binding.source.fingerprint.decode("ascii"),
            binding.target.fingerprint.decode("ascii"),
        )
        save_status(store, status)
        store.set(GATE, migration_id.encode("ascii"))
        store.set(schema_path(status.global_name), marker(migration_id))
        return status

    return store.atomic(claim)


def _check_manifests(store: Store, status: MigrationStatus) -> tuple[bytes, bytes]:
    root = job_path(status.migration_id)
    source = store.get(root.child(b"source_snapshot"))
    target = store.get(root.child(b"target_snapshot"))
    if source is None or target is None:
        raise StorageError("Migration snapshots are incomplete")
    for data, fingerprint in (
        (source, status.source_fingerprint),
        (target, status.target_fingerprint),
    ):
        snapshot = SchemaSnapshot(data)
        if snapshot.global_name != status.global_name or snapshot.fingerprint != fingerprint:
            raise StorageError("Migration snapshot does not match its checkpoint")
    if store.get(schema_path(status.global_name).child(b"snapshot")) != source:
        raise StorageError("Installed source snapshot changed during migration")
    return source, target


def _check_metadata(store: Store, spec: ModelSpec, root: Path) -> None:
    if any(store.get(root.child(b"%m", key)) != value for key, value in metadata(spec).items()):
        raise StorageError("A migration record has incompatible or incomplete metadata")


def _field_nodes(spec: ModelSpec, root: Path, record: Model) -> dict[Path, bytes]:
    return {
        path: value
        for path, value in record_nodes(spec, root, record).items()
        if path.subscripts[len(root.subscripts)] != b"%m"
    }


def _validate_models(binding: _Binding, values: dict[str, Any]) -> tuple[Model, Model]:
    try:
        old = binding.source.model.model_validate(deepcopy(values))
        new = binding.target.model.model_validate(
            {**deepcopy(values), **deepcopy(binding.defaults)}
        )
    except ValidationError:
        raise MigrationError(
            "A stored record does not satisfy the migration model bindings"
        ) from None
    return old, new


def _validate_targets(store: Store, binding: _Binding, links: frozenset[references.Link]) -> None:
    references.replace(store, links, links)  # Validate existing edges without changing counts.
    for link in links:
        spec = binding.registry.spec(link.target_model)
        if spec.global_name == binding.target.global_name:
            # A retag batch may encounter an already-retagged self-reference.
            fingerprint = store.get(link.target.child(b"%m", b"fingerprint"))
            spec = binding.target if fingerprint == binding.target.fingerprint else binding.source
        elif store.get(schema_path(spec.global_name)) != spec.fingerprint:
            raise StorageError("A reference target has an incompatible installed schema")
        _check_metadata(store, spec, link.target)


@dataclass(frozen=True)
class _Record:
    root: Path
    additions: dict[Path, bytes]
    old_entries: frozenset[indexes.Entry]
    new_entries: frozenset[indexes.Entry]


def _prepare(store: Store, binding: _Binding, key: bytes, phase: str) -> _Record:
    root = binding.source.path(decode_key(key))
    _check_metadata(store, binding.source, root)
    values = record_values(binding.source, root, store)
    old, new = _validate_models(binding, values)
    if binding.target.path(getattr(new, binding.target.key_field)) != root:
        raise MigrationError("Migration validation changed a record identity")
    old_nodes = _field_nodes(binding.source, root, old)
    new_nodes = _field_nodes(binding.target, root, new)
    if any(
        new_nodes.get(path) != value or store.get(path) != value
        for path, value in old_nodes.items()
    ):
        raise MigrationError("Migration validation would change an existing field")
    additions = {path: value for path, value in new_nodes.items() if path not in old_nodes}
    _check_additions(store, additions, phase)
    old_entries = indexes.entries(binding.source, root, old)
    new_entries = indexes.entries(binding.target, root, new)
    if not old_entries <= new_entries:
        raise MigrationError("Migration would change existing index identities")
    indexes.replace(store, old_entries, old_entries)
    _check_added_entries(store, new_entries - old_entries, phase)
    _validate_targets(store, binding, references.links(binding.registry, binding.target, root, new))
    return _Record(root, additions, old_entries, new_entries)


def _check_additions(store: Store, additions: dict[Path, bytes], phase: str) -> None:
    for path, value in additions.items():
        expected = value if phase in {"verify", "retag"} else None
        if store.get(path) != expected or store.children(path, limit=1):
            raise StorageError("A backfill field is missing, corrupt, or already occupied")


def _check_added_entries(store: Store, entries: frozenset[indexes.Entry], phase: str) -> None:
    if phase in {"verify", "retag"}:
        indexes.replace(store, entries, entries)
    elif any(store.get(entry.path) is not None for entry in entries):
        raise StorageError("An unexpected backfill index entry already exists")


def _process(store: Store, binding: _Binding, key: bytes, phase: str) -> None:
    record = _prepare(store, binding, key, phase)
    if phase == "backfill":
        for path, value in record.additions.items():
            store.set(path, value)
        indexes.replace(store, record.old_entries, record.new_entries)
    elif phase == "retag":
        for name, value in metadata(binding.target).items():
            store.set(record.root.child(b"%m", name), value)


def _check_counts(status: MigrationStatus) -> None:
    counts = (status.validated, status.backfilled, status.verified, status.retagged)
    index = ("validate", "backfill", "verify", "retag", "activate", "complete").index(status.phase)
    if len(set(counts[:index])) > 1 or any(counts[index + 1 :]):
        raise StorageError("Migration counters do not match the current phase")


def _activate(store: Store, status: MigrationStatus, target: bytes) -> MigrationStatus:
    root = schema_path(status.global_name)
    store.set(root.child(b"snapshot"), target)
    store.set(root, status.target_fingerprint.encode("ascii"))
    completed = replace(status, phase="complete", cursor=b"")
    save_status(store, completed)
    store.delete_tree(GATE)
    return completed


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
        manifests = _check_manifests(store, status)
        if manifests != (binding.source.snapshot, binding.target.snapshot):
            raise MigrationError("Resume bindings do not match the saved migration snapshots")
        _check_counts(status)
        if status.phase == "activate":
            return _activate(store, status, manifests[1])
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
        _process(store, binding, key, status.phase)
    counter = _COUNTER[status.phase]
    changes: dict[str, Any] = {counter: getattr(status, counter) + len(keys)}
    if keys:
        changes["cursor"] = keys[-1]
    else:
        changes.update(phase=_NEXT[status.phase], cursor=b"")
    updated = replace(status, **changes)
    _check_counts(updated)
    save_status(store, updated)
    return updated


def abort(store: Store, migration_id: str) -> MigrationStatus:
    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        if status.phase == "aborted":
            return status
        if status.phase == "complete" or status.backfilled or status.retagged:
            raise MigrationError("A committed backfill must be resumed; it cannot be aborted")
        require_owner(store, status)
        source, _ = _check_manifests(store, status)
        store.set(
            schema_path(status.global_name), SchemaSnapshot(source).fingerprint.encode("ascii")
        )
        result = replace(status, phase="aborted", cursor=b"")
        save_status(store, result)
        store.delete_tree(GATE)
        return result

    return store.atomic(attempt)
