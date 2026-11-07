# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded key and equality-index plans with explicit record scans and audits."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from . import index_scans, indexes, occurrences, query_intersections
from .codecs import Key, decode_key, encode_key
from .errors import NotFound, QueryError, SchemaError, StorageError
from .models import Model, Reference
from .mutations import read_owner
from .owned import OwnedPath, Owner, require_object, resolve_collection
from .query_intersections import RootPage
from .registry import reference_target, scalar_type
from .selectors import FieldPredicate, QueryField, bind_field
from .storage import Path

if TYPE_CHECKING:
    from .repository import Database

MAX_QUERY_RECORDS = 1000


def _limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= MAX_QUERY_RECORDS:
        raise QueryError("Query limits must be between 1 and 1000")


@dataclass(frozen=True)
class PredicatePlan:
    strategy: Literal["direct_key", "equality_index", "ordered_index"]
    field: str
    operation: Literal["equality", "range", "prefix", "ordered"]
    maximum_index_visits: int = index_scans.MAX_VISITS


@dataclass(frozen=True)
class QueryPlan:
    strategy: Literal["direct_key", "equality_index", "ordered_index", "index_intersection"]
    global_name: str
    field: str
    maximum_limit: int = MAX_QUERY_RECORDS
    maximum_index_visits: int | None = None
    predicates: tuple[PredicatePlan, ...] = ()
    order_field: str | None = None
    order_direction: Literal["asc", "desc"] | None = None


def _predicate_plan(term: query_intersections.Predicate) -> PredicatePlan:
    if term.selection is not None:
        return PredicatePlan("ordered_index", term.name, term.selection.kind)
    strategy: Literal["direct_key", "equality_index"] = (
        "direct_key" if term.definition is None else "equality_index"
    )
    return PredicatePlan(strategy, term.name, "equality")


class Query[M: Model]:
    def __init__(
        self,
        db: "Database",
        model: type[M],
        *,
        field: str | None = None,
        value: Any = None,
        definition: indexes.Definition | None = None,
        selection: index_scans.Selection | None = None,
        predicates: tuple[query_intersections.Predicate, ...] = (),
    ) -> None:
        self._db = db
        self._model = model
        self._field = field
        self._value = value
        self._definition = definition
        self._selection = selection
        self._predicates = predicates

    def _term(self) -> query_intersections.Predicate:
        assert self._field is not None
        return query_intersections.Predicate(
            self._field, self._definition, self._value, self._selection
        )

    def _combine(self, queries: tuple["Query[M]", ...]) -> "Query[M]":
        current = self._predicates or ((self._term(),) if self._field is not None else ())
        predicates = current + tuple(query._term() for query in queries)
        query_intersections.check_predicates(predicates)
        anchor = self if self._field is not None else queries[0]
        return Query(
            self._db,
            self._model,
            field=anchor._field,
            value=anchor._value,
            definition=anchor._definition,
            selection=anchor._selection,
            predicates=predicates,
        )

    def _ordered(self, name: str) -> indexes.Definition:
        self._db._ensure_active()
        definitions = indexes.definitions(self._db.registry.spec(self._model))
        definition = next((d for d in definitions if d.name == name), None)
        if definition is None or definition.field.index_codec != "ordered-v1":
            raise QueryError(
                f"Field {name!r} requires a declared ordered index; no scan is implied"
            )
        return definition

    def _selected(self, selection: index_scans.Selection) -> "Query[M]":
        selected = Query(
            self._db,
            self._model,
            field=selection.definition.name,
            definition=selection.definition,
            selection=selection,
        )
        return selected if self._field is None else self._combine((selected,))

    def range(
        self, field: str, *, gt: Any = None, ge: Any = None, lt: Any = None, le: Any = None
    ) -> "Query[M]":
        definition = self._ordered(field)
        if (gt is not None and ge is not None) or (lt is not None and le is not None):
            raise QueryError("Use one lower and one upper range bound")
        lower, upper = gt if gt is not None else ge, lt if lt is not None else le
        if lower is None and upper is None:
            raise QueryError("Specify at least one non-null range bound")
        lower = definition.validate(lower) if lower is not None else None
        upper = definition.validate(upper) if upper is not None else None
        for value in (lower, upper):
            if value is not None:
                indexes.index_root(self._db.registry.spec(self._model), definition, value)
        if (
            lower is not None
            and upper is not None
            and index_scans.primitive(lower) > index_scans.primitive(upper)
        ):
            raise QueryError("Lower range bound exceeds upper bound")
        return self._selected(
            index_scans.Selection(definition, "range", lower, upper, gt is None, lt is None)
        )

    def prefix(self, field: str, text: str) -> "Query[M]":
        definition = self._ordered(field)
        choices = definition.field.choices
        annotation = scalar_type(definition.owner.model_fields[definition.field.name].annotation)
        if choices is not None:
            annotation = type(choices.values[0])
        if annotation is not str or type(text) is not str:
            raise QueryError("Prefix queries require a string field and a literal string prefix")
        indexes.index_root(self._db.registry.spec(self._model), definition, text)
        return self._selected(index_scans.Selection(definition, "prefix", text=text))

    def order_by(
        self, field: str | QueryField[M, Any], *, direction: Literal["asc", "desc"] = "asc"
    ) -> "Query[M]":
        self._db._ensure_active()
        if direction not in ("asc", "desc"):
            raise QueryError("Order direction must be 'asc' or 'desc'")
        if isinstance(field, QueryField):
            field = bind_field(self._db.registry.spec(self._model), field)
        selection = index_scans.Selection(self._ordered(field), "ordered", direction=direction)
        if self._field is None:
            return self._selected(selection)
        if self._selection is not None and self._selection.kind == "ordered":
            raise QueryError("Specify order_by once after the predicates")
        if self._selection is not None and self._field == field and not self._predicates:
            return Query(
                self._db,
                self._model,
                field=field,
                definition=self._definition,
                selection=replace(self._selection, direction=direction),
            )
        return Query(
            self._db,
            self._model,
            field=field,
            definition=selection.definition,
            selection=selection,
            predicates=self._predicates or (self._term(),),
        )

    def where(self, *predicates: FieldPredicate[M], **equals: Any) -> "Query[M]":
        self._db._ensure_active()
        count = len(predicates) + len(equals)
        if not 1 <= count <= query_intersections.MAX_PREDICATES:
            raise QueryError("Specify between one and eight equality predicates")
        spec = self._db.registry.spec(self._model)
        terms = tuple((bind_field(spec, p.field), p.value) for p in predicates) + tuple(
            equals.items()
        )
        queries = tuple(self._equality(name, value) for name, value in terms)
        return self._combine(queries) if self._field is not None or count > 1 else queries[0]

    def _equality(self, name: str, value: Any) -> "Query[M]":
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
        plans = tuple(_predicate_plan(term) for term in self._predicates or (self._term(),))
        if self._predicates:
            return QueryPlan(
                "index_intersection",
                spec.global_name,
                self._field,
                maximum_index_visits=index_scans.MAX_VISITS,
                predicates=plans,
                order_field=self._field if self._selection is not None else None,
                order_direction=self._selection.direction if self._selection is not None else None,
            )
        if self._selection is not None:
            return QueryPlan(
                "ordered_index",
                spec.global_name,
                self._field,
                maximum_index_visits=index_scans.MAX_VISITS,
                predicates=plans,
                order_field=self._field,
                order_direction=self._selection.direction,
            )
        strategy: Literal["direct_key", "equality_index"] = (
            "equality_index" if self._definition is not None else "direct_key"
        )
        return QueryPlan(strategy, spec.global_name, self._field, predicates=plans)

    def all(self, *, limit: int = 100, after: Key | None = None) -> list[M]:
        """Return at most limit roots; bounded index work can fail before that ceiling."""
        _limit(limit)
        self.explain()
        if self._selection is not None:
            if after is not None and not isinstance(after, str):
                raise QueryError("Ordered reads require a root-page cursor, not a key cursor")
            return list(self._root_page(limit=limit, after=after, strict=True).records)
        if self._predicates:
            return cast(
                list[M],
                self._db._run(
                    lambda tx: query_intersections.roots(
                        tx,
                        tx.registry.spec(self._model),
                        self._term(),
                        self._predicates,
                        limit=limit,
                        after=after,
                    )
                ),
            )

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
            if tx.store.get(root) is not None:
                raise StorageError("Unexpected value at an equality-index namespace")
            keys = tx.store.children(root, limit=limit, after=cursor)
            result = []
            for key in keys:
                result.append(self._indexed_record(tx, key))
            return result

        return self._db._run(read)

    def page(self, *, limit: int = 100, after: str | None = None) -> RootPage[M]:
        """Continue an ordered root read using its canonical occurrence cursor."""
        return self._root_page(limit=limit, after=after, strict=False)

    def _root_page(self, *, limit: int, after: str | None, strict: bool) -> RootPage[M]:
        _limit(limit)
        self.explain()
        if self._selection is None:
            raise QueryError("Root page() requires an ordered index selection")
        return cast(
            RootPage[M],
            self._db._run(
                lambda tx: query_intersections.ordered_page(
                    tx,
                    tx.registry.spec(self._model),
                    self._term(),
                    self._predicates or (self._term(),),
                    limit=limit,
                    after=after,
                    strict=strict,
                )
            ),
        )

    def _indexed_record(self, tx: "Database", key: bytes) -> M:
        try:
            record = tx.get(self._model, decode_key(key))
        except NotFound as exc:
            raise StorageError("Index points to a missing record") from exc
        assert self._definition is not None
        occurrences.validate_root(
            tx, tx.registry.spec(self._model), self._definition, self._value, key, record
        )
        return record

    def occurrences(
        self, *, limit: int = 100, after: str | None = None
    ) -> list[occurrences.Occurrence]:
        """Return complete owners of equality occurrences at their concrete paths."""
        _limit(limit)
        self.explain()
        if self._predicates:
            raise QueryError(
                "Combined predicates return roots through all(); occurrences is ambiguous"
            )
        if self._definition is None:
            raise QueryError("Occurrence projection requires an equality index")
        definition = self._definition

        def read(tx: "Database") -> list[occurrences.Occurrence]:
            if self._selection is not None:
                return index_scans.page(
                    tx, tx.registry.spec(self._model), self._selection, limit=limit, after=after
                )
            return occurrences.page(
                tx, tx.registry.spec(self._model), definition, self._value, limit=limit, after=after
            )

        return self._db._run(read)


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


def referrers(
    db: "Database", reference: Reference[Any], *, source: type[Model], limit: int, after: str | None
) -> list[occurrences.Occurrence]:
    _limit(limit)
    return db._run(
        lambda tx: occurrences.inverse_page(tx, reference, source, limit=limit, after=after)
    )


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
