# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded key and equality-index plans with explicit record scans and audits."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from . import indexes
from .codecs import Key, decode_key
from .errors import NotFound, QueryError, StorageError
from .models import Model
from .records import walk_values
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
            annotation = self._model.model_fields[name].rebuild_annotation()
            validated = TypeAdapter(annotation).validate_python(value, strict=True)
            spec.path(validated)
            return Query(self._db, self._model, field=name, value=validated)
        definition = next((d for d in indexes.definitions(spec) if d.name == name), None)
        if definition is None:
            raise QueryError("Equality queries require a declared index; no scan is implied")
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
