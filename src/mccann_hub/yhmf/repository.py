# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit CRUD with atomic schema checks and retry-local transaction handles."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from . import indexes, migration_executor, migration_jobs, references
from .codecs import Key
from .errors import (
    AlreadyExists,
    NotFound,
    ReferenceViolation,
    SchemaMismatch,
    StorageError,
    TransactionError,
)
from .maintenance import MigrationStatus, load_status, require_available
from .migrations import MigrationPlan, build_plan
from .models import Model
from .queries import IndexReport, Query, audit_indexes, scan
from .records import metadata, record_nodes, record_values
from .registry import ModelSpec, Registry
from .schemas import SchemaInspection, inspect_schema, schema_path
from .storage import MAX_VALUE_BYTES, Path, Store


class Database:
    """Persist registered models. Every standalone operation uses a transaction."""

    def __init__(self, store: Store, registry: Registry) -> None:
        self.store = store
        self.registry = registry
        registry.validate_references()
        self._transaction = False
        self._active = True
        self._rollback_only = False

    def _ensure_active(self) -> None:
        if not self._active:
            raise TransactionError("This transaction handle has expired")
        if self._rollback_only:
            raise TransactionError("Transaction must roll back after a failed write")

    @contextmanager
    def _writing(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self._rollback_only = True
            raise

    def atomic[T](self, callback: Callable[["Database"], T]) -> T:
        """Execute callback with a fresh handle on every native transaction attempt."""
        self._ensure_active()
        if self._transaction:
            raise TransactionError("Nested atomic callbacks are not supported")

        def attempt() -> T:
            tx = Database(self.store, self.registry)
            tx._transaction = True
            try:
                result = callback(tx)
                tx._ensure_active()
                return result
            finally:
                tx._active = False

        return self.store.atomic(attempt)

    def _run[T](self, operation: Callable[["Database"], T]) -> T:
        self._ensure_active()
        return operation(self) if self._transaction else self.atomic(operation)

    def _check_schema(self, spec: ModelSpec, *, install: bool = False) -> None:
        require_available(self.store)
        root = schema_path(spec.global_name)
        current = self.store.get(root)
        if current is None:
            if install:
                if len(spec.snapshot) > MAX_VALUE_BYTES:
                    raise StorageError("Schema snapshot exceeds the node limit")
                self.store.set(root, spec.fingerprint)
                self.store.set(root.child(b"snapshot"), spec.snapshot)
        elif current != spec.fingerprint:
            raise SchemaMismatch("Installed schema differs; an explicit migration is required")

    def _exists(self, spec: ModelSpec, root: Path) -> bool:
        layout = self.store.get(root.child(b"%m", b"layout"))
        if layout is None:
            if self.store.get(root) is not None or self.store.children(root, limit=1):
                raise StorageError("Unmanaged or incomplete record at the requested path")
            return False
        for name, value in metadata(spec).items():
            if self.store.get(root.child(b"%m", name)) != value:
                raise SchemaMismatch("Record metadata differs from the registered schema")
        if self.store.get(schema_path(spec.global_name)) != spec.fingerprint:
            raise SchemaMismatch("Record has no matching installed schema")
        return True

    def get[M: Model](self, model: type[M], key: Key) -> M:
        """Read a complete record or raise NotFound; never fill missing stored fields."""
        spec = self.registry.spec(model)
        root = spec.path(key)

        def read(tx: Database) -> M:
            tx._check_schema(spec)
            if not tx._exists(spec, root):
                raise NotFound("Record does not exist")
            result = model.model_validate(record_values(spec, root, tx.store))
            if getattr(result, spec.key_field) != key:
                raise StorageError("Stored primary key does not match its path")
            return result

        return self._run(read)

    def _write[M: Model](self, record: M, *, insert: bool) -> M:
        spec = self.registry.spec(type(record))
        values = {field.name: getattr(record, field.name) for field in spec.fields}
        validated = type(record).model_validate(values)
        root = spec.path(getattr(validated, spec.key_field))
        nodes = record_nodes(spec, root, validated)
        new_links = references.links(self.registry, spec, root, validated)
        new_entries = indexes.entries(spec, root, validated)

        def write(tx: Database) -> M:
            tx._check_schema(spec)
            exists = tx._exists(spec, root)
            if insert and exists:
                raise AlreadyExists("Insert cannot replace an existing record")
            old_links: frozenset[references.Link] = frozenset()
            old_entries: frozenset[indexes.Entry] = frozenset()
            if exists:
                previous = tx.get(type(record), getattr(validated, spec.key_field))
                old_links = references.links(tx.registry, spec, root, previous)
                old_entries = indexes.entries(spec, root, previous)
            with tx._writing():
                tx._check_schema(spec, install=True)
                tx.store.delete_tree(root)
                for path, value in nodes.items():
                    tx.store.set(path, value)
                tx._check_reference_targets(new_links)
                references.replace(tx.store, old_links, new_links)
                indexes.replace(tx.store, old_entries, new_entries)
            return validated

        return self._run(write)

    def _check_reference_targets(self, links: frozenset[references.Link]) -> None:
        targets = {(link.target_model, link.target) for link in links}
        for model, path in targets:
            spec = self.registry.spec(model)
            self._check_schema(spec)
            if not self._exists(spec, path):
                raise ReferenceViolation("Reference target does not exist")

    def insert[M: Model](self, record: M) -> M:
        """Insert a new validated record, refusing to overwrite existing data."""
        return self._write(record, insert=True)

    def save[M: Model](self, record: M) -> M:
        """Replace the entire record subtree, or insert when the key is absent."""
        return self._write(record, insert=False)

    def delete(self, model: type[Model], key: Key) -> bool:
        """Delete a complete subtree. Return whether a record existed."""
        spec = self.registry.spec(model)
        root = spec.path(key)

        def remove(tx: Database) -> bool:
            tx._check_schema(spec)
            exists = tx._exists(spec, root)
            if exists:
                record = tx.get(model, key)
                old_links = references.links(tx.registry, spec, root, record)
                old_entries = indexes.entries(spec, root, record)
                with tx._writing():
                    references.replace(tx.store, old_links, frozenset())
                    references.restrict_delete(tx.store, root)
                    indexes.replace(tx.store, old_entries, frozenset())
                    tx.store.delete_tree(root)
            return exists

        return self._run(remove)

    def query[M: Model](self, model: type[M]) -> Query[M]:
        self._ensure_active()
        self.registry.spec(model)
        return Query(self, model)

    def scan[M: Model](
        self, model: type[M], *, limit: int = 100, after: Key | None = None
    ) -> list[M]:
        """Explicitly enumerate a bounded page of root records in native key order."""
        return scan(self, model, limit=limit, after=after)

    def check_indexes(
        self,
        model: type[Model],
        *,
        limit: int = 100,
        after: Key | None = None,
    ) -> IndexReport:
        """Check expected entries and claims for a bounded page of records."""
        return audit_indexes(self, model, limit=limit, after=after)

    def inspect_schema(self, model: type[Model]) -> SchemaInspection:
        """Inspect metadata atomically without requiring a matching installed schema."""
        spec = self.registry.spec(model)
        return self._run(lambda tx: inspect_schema(tx.store, spec))

    def plan_migration(self, model: type[Model]) -> MigrationPlan:
        """Describe schema changes without scanning, validating, or modifying records."""
        return build_plan(self.inspect_schema(model))

    def _migration_ready(self) -> None:
        self._ensure_active()
        if self._transaction:
            raise TransactionError("Migration lifecycle operations require their own transactions")

    def begin_migration(
        self, source: type[Model], target: type[Model], *, plan: MigrationPlan
    ) -> MigrationStatus:
        """Claim maintenance after checking an explicitly reviewed plan and model bindings."""
        self._migration_ready()
        return migration_executor.begin(self.store, self.registry, source, target, plan)

    def advance_migration(
        self,
        migration_id: str,
        source: type[Model],
        target: type[Model],
        *,
        batch_size: int = 25,
    ) -> MigrationStatus:
        """Commit at most one bounded batch or phase transition, resuming a saved job."""
        self._migration_ready()
        return migration_executor.advance(
            self.store, self.registry, migration_id, source, target, batch_size=batch_size
        )

    def migration_status(self, migration_id: str) -> MigrationStatus:
        """Read a durable checkpoint without requiring ordinary data access."""
        return self._run(lambda tx: load_status(tx.store, migration_id))

    def abort_migration(self, migration_id: str) -> MigrationStatus:
        """Release maintenance before any update batch or cleanup deletion commits."""
        self._migration_ready()
        return migration_jobs.abort(self.store, migration_id)
