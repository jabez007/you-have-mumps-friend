# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit CRUD with atomic schema checks and retry-local transaction handles."""

from collections.abc import Callable
from typing import Any, cast

from .codecs import LAYOUT_VERSION, Key, Scalar, decode_scalar, encode_scalar
from .errors import AlreadyExists, NotFound, SchemaMismatch, StorageError, TransactionError
from .models import Model
from .registry import ModelSpec, Registry
from .storage import MAX_VALUE_BYTES, Path, Store


def _schema_path(spec: ModelSpec) -> Path:
    return Path("^%YHMFSchema", (spec.global_name[1:].encode("ascii"),))


def _metadata(spec: ModelSpec) -> dict[bytes, bytes]:
    return {
        b"layout": str(LAYOUT_VERSION).encode("ascii"),
        b"schema": str(spec.model.__schema_version__).encode("ascii"),
        b"type": spec.global_name[1:].encode("ascii"),
        b"fingerprint": spec.fingerprint,
    }


def _record_nodes(spec: ModelSpec, root: Path, record: Model) -> dict[Path, bytes]:
    nodes = {root.child(b"%m", name): value for name, value in _metadata(spec).items()}
    for name in spec.fields:
        value = cast(Scalar, getattr(record, name))
        nodes[root.child(name.encode("ascii"))] = encode_scalar(value)
    if any(len(value) > MAX_VALUE_BYTES for value in nodes.values()):
        raise StorageError("Record contains a value exceeding the node limit")
    return nodes


class Database:
    """Persist registered models. Every standalone operation uses a transaction."""

    def __init__(self, store: Store, registry: Registry) -> None:
        self.store = store
        self.registry = registry
        self._transaction = False
        self._active = True

    def _ensure_active(self) -> None:
        if not self._active:
            raise TransactionError("This transaction handle has expired")

    def atomic[T](self, callback: Callable[["Database"], T]) -> T:
        """Execute callback with a fresh handle on every native transaction attempt."""
        self._ensure_active()
        if self._transaction:
            raise TransactionError("Nested atomic callbacks are not supported")

        def attempt() -> T:
            tx = Database(self.store, self.registry)
            tx._transaction = True
            try:
                return callback(tx)
            finally:
                tx._active = False

        return self.store.atomic(attempt)

    def _run[T](self, operation: Callable[["Database"], T]) -> T:
        self._ensure_active()
        return operation(self) if self._transaction else self.atomic(operation)

    def _check_schema(self, spec: ModelSpec, *, install: bool = False) -> None:
        root = _schema_path(spec)
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
        for name, value in _metadata(spec).items():
            if self.store.get(root.child(b"%m", name)) != value:
                raise SchemaMismatch("Record metadata differs from the registered schema")
        if self.store.get(_schema_path(spec)) != spec.fingerprint:
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
            values: dict[str, Any] = {}
            for name in spec.fields:
                stored = tx.store.get(root.child(name.encode("ascii")))
                if stored is None:
                    raise StorageError("A declared field is missing from storage")
                values[name] = decode_scalar(stored)
            result = model.model_validate(values)
            if getattr(result, spec.key_field) != key:
                raise StorageError("Stored primary key does not match its path")
            return result

        return self._run(read)

    def _write[M: Model](self, record: M, *, insert: bool) -> M:
        spec = self.registry.spec(type(record))
        validated = type(record).model_validate(record.model_dump(mode="python", round_trip=True))
        root = spec.path(getattr(validated, spec.key_field))
        nodes = _record_nodes(spec, root, validated)

        def write(tx: Database) -> M:
            tx._check_schema(spec)
            exists = tx._exists(spec, root)
            if insert and exists:
                raise AlreadyExists("Insert cannot replace an existing record")
            tx._check_schema(spec, install=True)
            tx.store.delete_tree(root)
            for path, value in nodes.items():
                tx.store.set(path, value)
            return validated

        return self._run(write)

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
                tx.store.delete_tree(root)
            return exists

        return self._run(remove)
