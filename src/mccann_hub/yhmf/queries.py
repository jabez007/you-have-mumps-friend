# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded key and equality-index plans with explicit record scans and audits."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from . import indexes
from .codecs import Key, decode_key, encode_key
from .errors import NotFound, QueryError, SchemaError, StorageError
from .maintenance import require_available
from .models import Model, Reference
from .mutations import read_owner
from .owned import OwnedPath, Owner, require_object, resolve_collection
from .records import walk_values
from .registry import reference_target
from .storage import Path

if TYPE_CHECKING:
    from .repository import Database

MAX_QUERY_RECORDS = 1000


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_QUERY_RECORDS:
        raise QueryError("Query limits must be between 1 and 1000")


@dataclass(frozen=True)
class QueryPlan:
    strategy: Literal["direct_key", "equality_index"]
    global_name: str
    field: str
    maximum_limit: int = MAX_QUERY_RECORDS


class Query[M: Model]:
    def __init__(
        self,
        db: "Database",
        model: type[M],
        *,
        field: str | None = None,
        value: Any = None,
        definition: indexes.Definition | None = None,
    ) -> None:
        self._db = db
        self._model = model
        self._field = field
        self._value = value
        self._definition = definition

    def where(self, **equals: Any) -> "Query[M]":
        self._db._ensure_active()
        if self._field is not None or len(equals) != 1:
            raise QueryError("Queries support exactly one equality predicate")
        name, value = next(iter(equals.items()))
        spec = self._db.registry.spec(self._model)
        if name == spec.key_field:
            field = next(field for field in spec.fields if field.name == name)
            assert field.adapter is not None
            validated = field.adapter.validate_python(value)
            spec.path(validated)
            return Query(self._db, self._model, field=name, value=validated)
        definitions = indexes.definitions(spec)
        definition = next((d for d in definitions if d.name == name), None)
        if definition is None:
            available = ", ".join(sorted({spec.key_field, *(d.name for d in definitions)}))
            raise QueryError(
                f"Equality field {name!r} has no declared index; use one of: {available}; "
                "no scan is implied"
            )
        validated = definition.validate(value)
        indexes.index_root(spec, definition, validated)
        return Query(self._db, self._model, field=name, value=validated, definition=definition)

    def explain(self) -> QueryPlan:
        self._db._ensure_active()
        if self._field is None:
            raise QueryError("Specify an indexed equality or primary key; use scan for enumeration")
        spec = self._db.registry.spec(self._model)
        strategy: Literal["direct_key", "equality_index"] = (
            "equality_index" if self._definition is not None else "direct_key"
        )
        return QueryPlan(strategy, spec.global_name, self._field)

    def all(self, *, limit: int = 100, after: Key | None = None) -> list[M]:
        _limit(limit)
        self.explain()

        def read(tx: "Database") -> list[M]:
            spec = tx.registry.spec(self._model)
            tx._check_schema(spec)
            if self._definition is None:
                if after is not None:
                    raise QueryError("Direct-key lookups do not use pagination cursors")
                try:
                    return [tx.get(self._model, self._value)]
                except NotFound:
                    return []
            cursor = spec.path(after).subscripts[0] if after is not None else b""
            root = indexes.index_root(spec, self._definition, self._value)
            keys = tx.store.children(root, limit=limit, after=cursor)
            result = []
            for key in keys:
                result.append(self._indexed_record(tx, key))
            return result

        return self._db._run(read)

    def _indexed_record(self, tx: "Database", key: bytes) -> M:
        try:
            record = tx.get(self._model, decode_key(key))
        except NotFound as exc:
            raise StorageError("Index points to a missing record") from exc
        assert self._definition is not None
        expected = indexes.normalized(self._value)
        matches = any(
            field is self._definition.field and indexes.normalized(value) == expected
            for field, _, value in walk_values(tx.registry.spec(self._model).fields, record)
        )
        if not matches:
            raise StorageError("Index value does not match its record")
        return record


def scan[M: Model](
    db: "Database",
    model: type[M],
    *,
    limit: int = 100,
    after: Key | None = None,
) -> list[M]:
    _limit(limit)
    spec = db.registry.spec(model)
    root = spec.path(after) if after is not None else None
    cursor = root.subscripts[0] if root is not None else b""

    def read(tx: "Database") -> list[M]:
        tx._check_schema(spec)
        keys = tx.store.children(Path(spec.global_name), limit=limit, after=cursor)
        return [tx.get(model, decode_key(key)) for key in keys]

    return db._run(read)


def children(
    db: "Database",
    model: type[Model],
    key: Key,
    *,
    collection: OwnedPath,
    limit: int,
    after: Key | None,
) -> list[Model]:
    _limit(limit)

    def read(tx: "Database") -> list[Model]:
        spec = tx.registry.spec(model)
        owner, field, path, count = resolve_collection(tx, spec, key, collection)
        assert field.node is not None and field.node.key_field is not None
        annotation = field.node.model.model_fields[field.node.key_field].annotation
        if after is not None and type(after) is not annotation:
            raise SchemaError("Cursor key type does not match the child declaration")
        cursor = b"" if after is None else encode_key(after)
        keys = tx.store.children(path, limit=limit, after=cursor)
        if len(keys) > count or (not cursor and len(keys) < limit and len(keys) != count):
            raise StorageError("Collection count disagrees with its page")
        result = []
        for child_key in keys:
            if type(decode_key(child_key)) is not annotation:
                raise StorageError("Stored child key type disagrees with its declaration")
            child = path.child(child_key)
            require_object(tx.store, child)
            relative = owner.relative + (field.name.encode("ascii"), child_key)
            result.append(read_owner(tx.store, Owner(field.node, child, relative)))
        return result

    return db._run(read)


def count_children(db: "Database", model: type[Model], key: Key, *, collection: OwnedPath) -> int:
    def read(tx: "Database") -> int:
        _, _, _, count = resolve_collection(tx, tx.registry.spec(model), key, collection)
        return count

    return db._run(read)


def _batch[T](values: Sequence[T]) -> tuple[T, ...]:
    if isinstance(values, str | bytes | bytearray) or not isinstance(values, Sequence):
        raise QueryError("Batch reads require a finite sequence")
    size = len(values)
    if size > MAX_QUERY_RECORDS:
        raise QueryError("Batch reads accept at most 1000 items")
    return tuple(values[index] for index in range(size))


def get_many[M: Model](db: "Database", model: type[M], keys: Sequence[Key]) -> list[M]:
    batch = _batch(keys)

    def read(tx: "Database") -> list[M]:
        spec = tx.registry.spec(model)
        tx._check_schema(spec)
        cache: dict[Key, M] = {}
        result = []
        for key in batch:
            spec.path(key)  # Validate before cache lookup: bool must not alias int.
            if key not in cache:
                cache[key] = tx.get(model, key)
            result.append(cache[key])
        return result

    return db._run(read)


def resolve_many[M: Model](db: "Database", refs: Sequence[Reference[M]]) -> list[M]:
    batch = _batch(refs)

    def read(tx: "Database") -> list[M]:
        require_available(tx.store)
        cache: dict[tuple[type[M], Key], M] = {}
        result = []
        for reference in batch:
            if not isinstance(reference, Reference):
                raise SchemaError("Resolution requires a concrete Reference target")
            model = cast(type[M], reference_target(type(reference)))
            tx.registry.spec(model).path(reference.key)
            identity = (model, reference.key)
            if identity not in cache:
                try:
                    cache[identity] = tx.get(model, reference.key)
                except NotFound:
                    raise NotFound("Reference target does not exist") from None
            result.append(cache[identity])
        return result

    return db._run(read)


@dataclass(frozen=True)
class IndexIssue:
    field: str
    kind: str


@dataclass(frozen=True)
class IndexReport:
    checked_records: int
    issues: tuple[IndexIssue, ...]
    next_key: Key | None


def _entry_issues(db: "Database", entry: indexes.Entry) -> list[IndexIssue]:
    field = entry.path.subscripts[1].decode("ascii")
    issues = []
    if db.store.get(entry.path) != entry.location:
        issues.append(IndexIssue(field, "missing_or_corrupt_entry"))
    if entry.claim_path is not None and db.store.get(entry.claim_path) != entry.claim:
        issues.append(IndexIssue(field, "missing_or_corrupt_claim"))
    return issues


def audit_indexes(
    db: "Database",
    model: type[Model],
    *,
    limit: int = 100,
    after: Key | None = None,
) -> IndexReport:
    _limit(limit)

    def inspect(tx: "Database") -> IndexReport:
        records = scan(tx, model, limit=limit, after=after)
        spec = tx.registry.spec(model)
        issues = []
        for record in records:
            root = spec.path(getattr(record, spec.key_field))
            for entry in indexes.entries(spec, root, record):
                issues.extend(_entry_issues(tx, entry))
        next_key = getattr(records[-1], spec.key_field) if len(records) == limit else None
        return IndexReport(len(records), tuple(issues), next_key)

    return db._run(inspect)
