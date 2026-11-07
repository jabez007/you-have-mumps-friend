# SPDX-License-Identifier: GPL-3.0-or-later
"""Reviewable index reconstruction from authoritative records with an unchanged schema."""

from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import ValidationError

from . import indexes, migration_claims, migration_cleanup, migration_jobs, references
from .codecs import LAYOUT_VERSION, canonical_json, decode_key
from .errors import MigrationError, SchemaMismatch, StorageError
from .maintenance import MigrationStatus, load_status, require_owner, save_status
from .migration_records import check_metadata, field_nodes, validate_targets
from .models import Model
from .records import record_values
from .registry import ModelSpec, Registry
from .schema_compatibility import bind_snapshot
from .schemas import SchemaInspection, SchemaSnapshot
from .storage import Path, Store

_COUNTER = {"validate": "validated", "backfill": "backfilled", "verify": "verified"}
_NEXT = {"validate": "clear_indexes", "backfill": "verify", "verify": "activate"}


@dataclass(frozen=True)
class IndexRebuildPlan:
    inspection: SchemaInspection = field(repr=False)
    fields: tuple[str, ...]
    unique_fields: tuple[str, ...]

    @property
    def execution_supported(self) -> bool:
        return (
            bool(self.fields)
            and self.inspection.status == "matching"
            and self.inspection.declared.layout_version == LAYOUT_VERSION
        )

    def to_dict(self) -> dict[str, Any]:
        steps = ["validate_records"]
        if self.unique_fields:
            steps.append("validate_uniqueness")
        steps.extend(("clear_indexes", "rebuild_indexes", "verify_indexes", "activate_schema"))
        return {
            "format_version": 1,
            "operation": "index_rebuild",
            "execution_supported": self.execution_supported,
            "status": self.inspection.status,
            "global": self.inspection.declared.global_name,
            "schema_fingerprint": self.inspection.declared.fingerprint,
            "indexes": [
                {"field": name, "unique": name in self.unique_fields} for name in self.fields
            ],
            "steps": steps,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict()).decode("ascii")


def build_plan(
    spec: ModelSpec, inspection: SchemaInspection, *, fields: tuple[str, ...] | None = None
) -> IndexRebuildPlan:
    definitions = {definition.name: definition for definition in indexes.definitions(spec)}
    if fields is not None and (
        type(fields) is not tuple or not fields or any(type(name) is not str for name in fields)
    ):
        raise MigrationError("Rebuild fields must be a nonempty tuple of indexed declaration paths")
    selected = tuple(sorted(definitions if fields is None else fields))
    if len(set(selected)) != len(selected) or any(name not in definitions for name in selected):
        raise MigrationError("Rebuild fields must be distinct declared indexes")
    unique = tuple(name for name in selected if definitions[name].field.unique)
    return IndexRebuildPlan(inspection, selected, unique)


def _spec(registry: Registry, model: type[Model]) -> ModelSpec:
    spec = Registry().register(model)
    if registry.spec(model).snapshot != spec.snapshot:
        raise MigrationError("Model declaration changed after registration")
    return spec


def begin(
    store: Store, registry: Registry, model: type[Model], reviewed: IndexRebuildPlan
) -> MigrationStatus:
    spec = _spec(registry, model)
    snapshot = SchemaSnapshot(spec.snapshot)
    if reviewed.inspection.declared != snapshot or reviewed.inspection.installed is None:
        raise MigrationError("Rebuild requires the exact installed model declaration")
    spec = _saved_spec(spec, reviewed.inspection.installed)
    plan = build_plan(spec, reviewed.inspection, fields=reviewed.fields)
    if (
        not plan.execution_supported
        or plan.fields != reviewed.fields
        or plan.unique_fields != reviewed.unique_fields
    ):
        raise MigrationError("Reviewed rebuild plan differs from declared indexes")
    operation = canonical_json(
        {"format_version": 1, "kind": "index_rebuild", "fields": list(plan.fields)}
    )
    return migration_jobs.begin(store, spec, spec, operation=operation)


def _saved_spec(spec: ModelSpec, snapshot: SchemaSnapshot) -> ModelSpec:
    try:
        return bind_snapshot(spec, snapshot)
    except SchemaMismatch:
        raise MigrationError("Rebuild declaration differs from the saved job") from None


def _selection(store: Store, status: MigrationStatus) -> tuple[str, ...]:
    if status.operation_fingerprint is None:
        raise MigrationError("Job is not an index rebuild")
    document = migration_jobs.load_operation(store, status)
    if document["kind"] != "index_rebuild":
        raise MigrationError("Job is not an index rebuild")
    if (
        set(document) != {"format_version", "kind", "fields"}
        or type(document["fields"]) is not list
    ):
        raise StorageError("Malformed rebuild operation")
    return tuple(document["fields"])


def _roots(spec: ModelSpec, plan: IndexRebuildPlan) -> tuple[Path, ...]:
    return tuple(
        Path(global_name, (spec.global_name[1:].encode("ascii"), name.encode("ascii")))
        for name in plan.fields
        for global_name in ("^YHMFIndex", "^YHMFUnique")
    )


def _record(store: Store, registry: Registry, spec: ModelSpec, key: bytes) -> Model:
    root = spec.path(decode_key(key))
    check_metadata(store, spec, root)
    try:
        record = spec.model.model_validate(record_values(spec, root, store))
    except ValidationError:
        raise MigrationError("A stored record does not satisfy the rebuild declaration") from None
    if spec.path(getattr(record, spec.key_field)) != root:
        raise MigrationError("Rebuild validation changed a record identity")
    if any(store.get(path) != value for path, value in field_nodes(spec, root, record).items()):
        raise MigrationError("Rebuild validation would change stored fields")
    validate_targets(store, registry, spec, spec, references.links(registry, spec, root, record))
    return record


def _process(
    store: Store,
    registry: Registry,
    spec: ModelSpec,
    plan: IndexRebuildPlan,
    status: MigrationStatus,
    key: bytes,
) -> None:
    record = _record(store, registry, spec, key)
    selected = frozenset(name.encode("ascii") for name in plan.fields)
    entries = frozenset(
        entry
        for entry in indexes.entries(spec, spec.path(decode_key(key)), record)
        if entry.path.subscripts[1] in selected
    )
    claims = frozenset(entry for entry in entries if entry.claim_path is not None)
    if status.phase == "validate":
        migration_claims.reserve(store, status.migration_id, claims)
    else:
        migration_claims.verify(store, status.migration_id, claims)
        if status.phase == "backfill":
            indexes.apply_transition(store, frozenset(), entries)
        else:
            indexes.check_transition(store, frozenset(), entries, updated=True)


def _counts(status: MigrationStatus) -> None:
    if (
        status.phase
        not in {"validate", "clear_indexes", "backfill", "verify", "activate", "complete"}
        or status.retagged
    ):
        raise StorageError("Invalid rebuild phase or metadata counter")
    if status.phase in {"validate", "clear_indexes"} and (status.backfilled or status.verified):
        raise StorageError("Rebuild update counters precede index clearing")
    if status.phase == "backfill" and status.verified:
        raise StorageError("Rebuild verification counter precedes reconstruction")
    if status.phase == "verify" and status.validated != status.backfilled:
        raise StorageError("Rebuild parent counts disagree")
    if (
        status.phase in {"activate", "complete"}
        and len({status.validated, status.backfilled, status.verified}) != 1
    ):
        raise StorageError("Rebuild parent counts disagree")


def _batch(
    store: Store,
    registry: Registry,
    spec: ModelSpec,
    plan: IndexRebuildPlan,
    status: MigrationStatus,
    limit: int,
) -> MigrationStatus:
    root = Path(spec.global_name)
    if store.get(root) is not None:
        raise StorageError("An unmanaged value occupies the application global root")
    keys = store.children(root, limit=limit, after=status.cursor)
    for key in keys:
        _process(store, registry, spec, plan, status, key)
    counter = _COUNTER[status.phase]
    changes: dict[str, Any] = {counter: getattr(status, counter) + len(keys)}
    if keys:
        changes["cursor"] = keys[-1]
    else:
        changes.update(phase=_NEXT[status.phase], cursor=b"")
    updated = replace(status, **changes)
    _counts(updated)
    save_status(store, updated)
    return updated


def advance(
    store: Store, registry: Registry, migration_id: str, model: type[Model], *, batch_size: int
) -> MigrationStatus:
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise MigrationError("Rebuild batch size must be between 1 and 100")
    declared = _spec(registry, model)

    def attempt() -> MigrationStatus:
        status = load_status(store, migration_id)
        fields = _selection(store, status)
        source, target = migration_jobs.saved_snapshots(store, status)
        spec = _saved_spec(declared, SchemaSnapshot(source))
        if source != target:
            raise MigrationError("Rebuild snapshots differ from the supplied model")
        snapshot = SchemaSnapshot(spec.snapshot)
        plan = build_plan(spec, SchemaInspection(snapshot, snapshot, True), fields=fields)
        if (
            status.source_fingerprint != snapshot.fingerprint
            or status.target_fingerprint != snapshot.fingerprint
        ):
            raise MigrationError("Rebuild declaration differs from the saved job")
        if status.phase == "aborted":
            raise MigrationError("Rebuild was aborted")
        _counts(status)
        if status.phase == "complete":
            return status
        require_owner(store, status)
        if migration_jobs.manifests(store, status) != (spec.snapshot, spec.snapshot):
            raise MigrationError("Rebuild snapshots differ from the supplied model")
        if status.phase == "activate":
            return migration_jobs.complete(store, status, spec.snapshot)
        if status.phase == "clear_indexes":
            deleted = migration_cleanup.clear_batch(
                store, migration_id, _roots(spec, plan), limit=batch_size
            )
            result = status if deleted else replace(status, phase="backfill", cursor=b"")
            save_status(store, result)
            return result
        return _batch(store, registry, spec, plan, status, batch_size)

    return store.atomic(attempt)
