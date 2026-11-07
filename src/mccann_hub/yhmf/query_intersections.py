# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit bounded index witnesses for conjunctive root queries."""

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import index_scans, indexes, occurrences
from .codecs import Key, canonical_json, decode_key, encode_key
from .errors import NotFound, QueryError, StorageError
from .models import Model
from .records import walk_values
from .registry import ModelSpec
from .storage import Path, _collation

if TYPE_CHECKING:
    from .repository import Database

MAX_PREDICATES = 8


@dataclass(frozen=True)
class RootPage[M: Model]:
    """A bounded root page; cursor resumes after its last fully checked position."""

    records: tuple[M, ...] = field(repr=False)
    cursor: str | None = field(repr=False)


@dataclass(frozen=True)
class Predicate:
    name: str
    definition: indexes.Definition | None = None
    value: Any = field(default=None, repr=False)
    selection: index_scans.Selection | None = None


def check_predicates(predicates: tuple[Predicate, ...]) -> None:
    if len(predicates) > MAX_PREDICATES:
        raise QueryError("Queries accept at most eight predicates")
    if len({term.name for term in predicates}) != len(predicates):
        raise QueryError("Combine each field's constraints in one predicate")


def _namespace(tx: "Database", prefix: Path) -> None:
    if tx.store.get(prefix) is not None:
        raise StorageError("Unexpected value at an index namespace")


def candidates(
    tx: "Database",
    spec: ModelSpec,
    anchor: Predicate,
    budget: index_scans.Budget,
    after: Key | None,
) -> Iterator[tuple[bytes, Any]]:
    if anchor.selection is not None:
        if after is not None:
            raise QueryError("Ordered root reads use no key cursor; narrow the selection")
        for value, path in index_scans.entries(
            tx.store, spec, anchor.selection, after=None, budget=budget
        ):
            assert anchor.definition is not None
            occurrences.read_indexed(tx, spec, anchor.definition, indexes.normalized(value), path)
            yield path.subscripts[-2], value
    elif anchor.definition is None:
        if after is not None:
            raise QueryError("Direct-key lookups do not use pagination cursors")
        budget.visit()
        yield encode_key(anchor.value), anchor.value
    else:
        prefix = indexes.index_root(spec, anchor.definition, anchor.value)
        _namespace(tx, prefix)
        cursor = spec.path(after).subscripts[0] if after is not None else b""
        while keys := tx.store.children(prefix, limit=1, after=cursor):
            budget.visit()
            cursor = keys[0]
            yield cursor, anchor.value


def witness(
    tx: "Database", spec: ModelSpec, term: Predicate, key: bytes, budget: index_scans.Budget
) -> tuple[bool, Any]:
    if term.definition is None:
        budget.visit()
        return key == encode_key(term.value), term.value
    if term.selection is None:
        budget.visit()
        prefix = indexes.index_root(spec, term.definition, term.value)
        _namespace(tx, prefix)
        root = prefix.child(key)
        _namespace(tx, root)
        return bool(tx.store.children(root, limit=1)), term.value
    for value, path in index_scans.entries(
        tx.store, spec, term.selection, after=None, budget=budget
    ):
        if path.subscripts[-2] == key:
            occurrences.read_indexed(tx, spec, term.definition, indexes.normalized(value), path)
            return True, value
    return False, None


def _record(
    tx: "Database",
    spec: ModelSpec,
    key: bytes,
    predicates: tuple[Predicate, ...],
    budget: index_scans.Budget,
    *,
    anchor: Predicate,
    anchor_value: Any,
) -> Model | None:
    witnesses = []
    for term in predicates:
        matches, value = (
            (True, anchor_value) if term == anchor else witness(tx, spec, term, key, budget)
        )
        if not matches:
            return None
        witnesses.append((term, value))
    try:
        record = tx.get(spec.model, decode_key(key))
    except NotFound:
        if anchor.definition is None and not any(
            term.definition is not None for term in predicates
        ):
            return None
        raise StorageError("Index points to a missing record") from None
    for term, value in witnesses:
        if term.definition is not None:
            occurrences.validate_root(tx, spec, term.definition, value, key, record)
    return record


def roots(
    tx: "Database",
    spec: ModelSpec,
    anchor: Predicate,
    predicates: tuple[Predicate, ...],
    *,
    limit: int,
    after: Key | None,
) -> list[Model]:
    spec = tx._check_schema(spec)
    budget = index_scans.Budget()
    result, seen = [], set()
    for key, value in candidates(tx, spec, anchor, budget, after):
        if key in seen:
            continue
        seen.add(key)
        record = _record(tx, spec, key, predicates, budget, anchor=anchor, anchor_value=value)
        if record is not None:
            result.append(record)
            if len(result) == limit:
                break
    return result


def _root_scope(spec: ModelSpec, anchor: Predicate, predicates: tuple[Predicate, ...]) -> str:
    assert anchor.selection is not None
    terms = [
        [
            term.name,
            index_scans.identity(spec, term.selection)
            if term.selection is not None
            else indexes.normalized(term.value).hex(),
        ]
        for term in sorted(predicates, key=lambda term: term.name)
    ]
    data = ["ordered-roots-v1", index_scans.identity(spec, anchor.selection), terms]
    return hashlib.sha256(canonical_json(data)).hexdigest()


def _representative(
    tx: "Database", spec: ModelSpec, selection: index_scans.Selection, key: bytes, record: Model
) -> Path:
    matches = (
        (field, path, value)
        for field, path, value in walk_values(spec.fields, record)
        if field is selection.definition.field and selection.accepts(value)
    )
    entries = indexes.entries_at(spec, spec.path(decode_key(key)), matches)
    if not entries:
        raise StorageError("Ordered root has no authoritative matching occurrence")
    choose = max if selection.direction == "desc" else min
    first = choose(
        entries,
        key=lambda entry: (_collation(entry.path.subscripts[-3]), entry.path.subscripts[-1]),
    )
    if tx.store.get(first.path) != first.location or tx.store.children(first.path, limit=1):
        raise StorageError("Canonical ordered root occurrence is missing or corrupt")
    indexes.validate_claim(tx.store, first)
    return first.path


def ordered_page(
    tx: "Database",
    spec: ModelSpec,
    anchor: Predicate,
    predicates: tuple[Predicate, ...],
    *,
    limit: int,
    after: str | None,
    strict: bool = False,
) -> RootPage[Model]:
    spec = tx._check_schema(spec)
    assert anchor.selection is not None and anchor.definition is not None
    selection, budget = anchor.selection, index_scans.Budget()
    scope = _root_scope(spec, anchor, predicates)
    result, seen, cursor = [], set(), None
    try:
        for value, path in index_scans.entries(
            tx.store, spec, selection, after=after, budget=budget, scope=scope
        ):
            occurrences.read_indexed(tx, spec, anchor.definition, indexes.normalized(value), path)
            if path.subscripts[-2] not in seen:
                record = _ordered_record(tx, spec, anchor, predicates, budget, value, path)
                seen.add(path.subscripts[-2])
                if record is not None:
                    result.append(record)
            cursor = index_scans.encode_cursor(scope, path.subscripts[-3], path)
            if len(result) == limit:
                return RootPage(tuple(result), cursor)
    except index_scans.VisitLimit:
        if strict or cursor is None:
            raise
        return RootPage(tuple(result), cursor)
    return RootPage(tuple(result), None)


def _ordered_record(
    tx: "Database",
    spec: ModelSpec,
    anchor: Predicate,
    predicates: tuple[Predicate, ...],
    budget: index_scans.Budget,
    value: Any,
    path: Path,
) -> Model | None:
    encoded = path.subscripts[-2]
    record = _record(tx, spec, encoded, predicates, budget, anchor=anchor, anchor_value=value)
    assert anchor.selection is not None
    if record is None or _representative(tx, spec, anchor.selection, encoded, record) != path:
        return None
    return record
