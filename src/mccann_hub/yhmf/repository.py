# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit CRUD with atomic schema checks and retry-local transaction handles."""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, overload

from . import (
    adoption,
    child_mutations,
    index_rebuilds,
    indexes,
    migration_executor,
    migration_jobs,
    mutations,
    queries,
    references,
)
from .codecs import Key
from .errors import (
    AlreadyExists,
    NotFound,
    ReferenceViolation,
    SchemaMismatch,
    StorageError,
    TransactionError,
)
from .index_rebuilds import IndexRebuildPlan
from .maintenance import MigrationStatus, load_status, require_available
from .migrations import MigrationPlan, build_plan
from .models import Model, Reference
from .occurrences import Occurrence
from .owned import OwnedPath
from .owned import resolve as resolve_owner
from .queries import IndexReport, Query, audit_indexes, count_children, scan
from .queries import children as read_children
from .records import input_values, metadata, record_nodes, record_values
from .registry import ModelSpec, Registry
from .schemas import SchemaInspection, check_schema, inspect_schema, schema_path
from .storage import Path, Store


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

    def _check_schema(self, spec: ModelSpec, *, install: bool = False) -> ModelSpec:
        require_available(self.store)
        return check_schema(self.store, spec, install=install)

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

    @overload
    def get[M: Model](self, model: type[M], key: Key) -> M: ...

    @overload
    def get(self, model: type[Model], key: Key, *, at: OwnedPath) -> Model: ...

    def get(self, model: type[Model], key: Key, *, at: OwnedPath = ()) -> Model:
        """Read a complete record or raise NotFound; never fill missing stored fields."""
        spec = self.registry.spec(model)
        if at != ():
            return self._run(lambda tx: mutations.read(tx, spec, key, at))
        root = spec.path(key)

        def read(tx: Database) -> Model:
            bound = tx._check_schema(spec)
            if not tx._exists(bound, root):
                raise NotFound("Record does not exist")
            result = model.model_validate(record_values(spec, root, tx.store))
            if getattr(result, spec.key_field) != key:
                raise StorageError("Stored primary key does not match its path")
            return result

        return self._run(read)

    def patch(
        self, model: type[Model], key: Key, *, changes: Mapping[str, Any], at: OwnedPath = ()
    ) -> None:
        """Validate and update scalar or Reference fields at an explicit owned path."""
        spec = self.registry.spec(model)

        def attempt(tx: Database) -> None:
            with tx._writing():
                mutations.patch(tx, spec, key, at, changes)

        self._run(attempt)

    def children(
        self,
        model: type[Model],
        key: Key,
        *,
        collection: OwnedPath,
        limit: int = 100,
        after: Key | None = None,
    ) -> list[Model]:
        """Read one bounded child page in native key order without loading the root."""
        return read_children(self, model, key, collection=collection, limit=limit, after=after)

    def count(self, model: type[Model], key: Key, *, collection: OwnedPath) -> int:
        """Read an owned collection's authoritative count marker, without a key scan."""
        return count_children(self, model, key, collection=collection)

    def get_many[M: Model](self, model: type[M], keys: Sequence[Key]) -> list[M]:
        """Read at most 1,000 ordered keys, fetching duplicate targets once per attempt."""
        return queries.get_many(self, model, keys)

    def resolve[M: Model](self, reference: Reference[M]) -> M:
        """Explicitly read a registered reference target in this transaction."""
        return self.resolve_many((reference,))[0]

    def resolve_many[M: Model](self, refs: Sequence[Reference[M]]) -> list[M]:
        """Resolve a bounded ordered batch, deduplicated only within this read attempt."""
        return queries.resolve_many(self, refs)

    def referrers(
        self,
        reference: Reference[Any],
        *,
        source: type[Model],
        limit: int = 100,
        after: str | None = None,
    ) -> list[Occurrence]:
        """Page exact reverse occurrences from a declared source without scanning roots."""
        return queries.referrers(self, reference, source=source, limit=limit, after=after)

    def raw_path(self, model: type[Model], key: Key, *, at: OwnedPath = ()) -> Path:
        """Resolve a managed native address; raw Store operations bypass mapper invariants."""
        self._ensure_active()
        if not self._transaction:
            raise TransactionError("raw_path requires an active atomic callback")
        return resolve_owner(self, self.registry.spec(model), key, at).path

    def insert_child(
        self, model: type[Model], key: Key, *, collection: OwnedPath, record: Model
    ) -> None:
        """Insert a validated keyed child into an existing owned collection."""
        spec = self.registry.spec(model)

        def attempt(tx: Database) -> None:
            with tx._writing():
                child_mutations.insert(tx, spec, key, collection, record)

        self._run(attempt)

    def delete_child(self, model: type[Model], key: Key, *, at: OwnedPath) -> None:
        """Delete an existing child subtree and its secondary occurrences."""
        spec = self.registry.spec(model)

        def attempt(tx: Database) -> None:
            with tx._writing():
                child_mutations.delete(tx, spec, key, at)

        self._run(attempt)

    def _write[M: Model](self, record: M, *, insert: bool) -> M:
        spec = self.registry.spec(type(record))
        values = input_values(spec.fields, record)
        validated = type(record).model_validate(values)
        root = spec.path(getattr(validated, spec.key_field))
        new_links = references.links(self.registry, spec, root, validated)
        new_entries = indexes.entries(spec, root, validated)

        def write(tx: Database) -> M:
            bound = tx._check_schema(spec)
            exists = tx._exists(bound, root)
            if insert and exists:
                raise AlreadyExists("Insert cannot replace an existing record")
            old_links: frozenset[references.Link] = frozenset()
            old_entries: frozenset[indexes.Entry] = frozenset()
            if exists:
                previous = tx.get(type(record), getattr(validated, spec.key_field))
                old_links = references.links(tx.registry, spec, root, previous)
                old_entries = indexes.entries(spec, root, previous)
            with tx._writing():
                bound = tx._check_schema(spec, install=True)
                nodes = record_nodes(bound, root, validated)
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
            spec = self._check_schema(spec)
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
            bound = tx._check_schema(spec)
            exists = tx._exists(bound, root)
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

    def adopt(self, model: type[Model]) -> int:
        """Explicitly adopt at most 100 exact layout-one records; never rewrite data."""
        self._migration_ready()
        spec = self.registry.spec(model)

        def attempt(tx: Database) -> int:
            require_available(tx.store)
            with tx._writing():
                return adoption.adopt(tx, spec)

        return self._run(attempt)

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

    def prune_migration(self, migration_id: str, *, batch_size: int = 25) -> bool:
        """Remove a terminal job in bounded batches; return True when nothing remains."""
        self._migration_ready()
        return migration_jobs.prune(self.store, migration_id, batch_size=batch_size)

    def plan_index_rebuild(
        self, model: type[Model], *, fields: tuple[str, ...] | None = None
    ) -> IndexRebuildPlan:
        """Review declared index namespaces without scanning or changing records."""
        spec = self.registry.spec(model)
        return index_rebuilds.build_plan(spec, self.inspect_schema(model), fields=fields)

    def begin_index_rebuild(self, model: type[Model], *, plan: IndexRebuildPlan) -> MigrationStatus:
        """Claim maintenance for a reviewed rebuild of an unchanged installed schema."""
        self._migration_ready()
        return index_rebuilds.begin(self.store, self.registry, model, plan)

    def advance_index_rebuild(
        self, migration_id: str, model: type[Model], *, batch_size: int = 25
    ) -> MigrationStatus:
        """Commit one bounded rebuild batch or transition using the saved selection."""
        self._migration_ready()
        return index_rebuilds.advance(
            self.store, self.registry, migration_id, model, batch_size=batch_size
        )
