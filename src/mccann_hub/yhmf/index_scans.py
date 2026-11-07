# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded native ordered-index walks and query-scoped concrete cursors."""

import base64
import binascii
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from . import indexes, occurrences, ordered
from .codecs import canonical_json, decode_scalar
from .errors import QueryError, StorageError
from .registry import ModelSpec, scalar_type
from .storage import Path, Store, child_page

if TYPE_CHECKING:
    from .repository import Database

MAX_VISITS = 1000


def primitive(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


@dataclass(frozen=True)
class Selection:
    definition: indexes.Definition
    kind: Literal["range", "prefix", "ordered"]
    lower: Any = field(default=None, repr=False)
    upper: Any = field(default=None, repr=False)
    include_lower: bool = True
    include_upper: bool = True
    text: str | None = field(default=None, repr=False)
    direction: Literal["asc", "desc"] = "asc"

    def accepts(self, value: Any) -> bool:
        value = primitive(value)
        if self.kind == "ordered":
            return True
        if self.kind == "prefix":
            return isinstance(value, str) and value.startswith(self.text or "")
        if value is None:
            return False
        low, high = primitive(self.lower), primitive(self.upper)
        return not (
            low is not None
            and (value < low or (value == low and not self.include_lower))
            or high is not None
            and (value > high or (value == high and not self.include_upper))
        )

    def start(self) -> tuple[bytes, bool]:
        if self.direction == "desc":
            if self.text is not None:
                # UTF-8 never emits FF; this is an exclusive upper prefix bound.
                return ordered.value_key(self.text) + b"\xff", False
            if self.upper is not None:
                return ordered.value_key(self.upper), self.include_upper
            return b"", False
        if self.text is not None:
            return ordered.value_key(self.text), True
        if self.lower is not None:
            return ordered.value_key(self.lower), self.include_lower
        return b"", False

    def finished(self, value: Any, token: bytes) -> bool:
        if self.text is not None:
            return not token.startswith(ordered.value_key(self.text))
        if self.kind != "range":
            return False
        return self._past_bound(primitive(value))

    def _past_bound(self, value: Any) -> bool:
        descending = self.direction == "desc"
        if value is None:
            return not descending
        bound = primitive(self.lower if descending else self.upper)
        inclusive = self.include_lower if descending else self.include_upper
        if bound is None:
            return False
        return (value < bound if descending else value > bound) or value == bound and not inclusive


def decode_value(definition: indexes.Definition, token: bytes) -> Any:
    """Decode and revalidate an ordered subscript without trusting index bytes."""
    choices = definition.field.choices
    annotation = scalar_type(definition.owner.model_fields[definition.field.name].annotation)
    if choices is not None:
        annotation = type(choices.values[0])
    try:
        value: Any
        if token == b"z:null":
            value = None
        elif annotation is str:
            raw = token[2:].replace(b"\x01\x01", b"\0").replace(b"\x01\x02", b"\x01")
            value = raw.decode("utf-8")
        elif annotation in (int, Decimal):
            value = annotation(token.decode("ascii"))
        else:
            value = decode_scalar(token)
        if choices is not None:
            value = choices.restore(value)
        result = definition.validate(value)
        if ordered.value_key(result) != token:
            raise ValueError
        return result
    except (ValueError, ArithmeticError, UnicodeError, StorageError):
        raise StorageError("Invalid ordered index value") from None


def identity(spec: ModelSpec, selection: Selection) -> str:
    start, inclusive = selection.start()
    data = [
        spec.fingerprint.decode("ascii"),
        spec.global_name,
        selection.definition.name,
        selection.kind,
        start.hex(),
        inclusive,
        None if selection.upper is None else ordered.value_key(selection.upper).hex(),
        selection.include_upper,
    ]
    if selection.direction == "desc":
        data.append("desc")
        if selection.kind == "range":
            data.extend(
                [
                    None if selection.lower is None else ordered.value_key(selection.lower).hex(),
                    selection.include_lower,
                ]
            )
    return hashlib.sha256(canonical_json(data)).hexdigest()


def encode_cursor(scope: str, value: bytes, path: Path) -> str:
    raw = canonical_json(
        [2, scope, value.hex(), path.subscripts[-2].hex(), path.subscripts[-1].decode("ascii")]
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(
    spec: ModelSpec, selection: Selection, token: str | None, *, scope: str | None = None
) -> tuple[bytes, bytes, bytes] | None:
    if token is None:
        return None
    try:
        if not isinstance(token, str) or len(token) > 4096:
            raise ValueError
        raw = base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if canonical_json(data) != raw or base64.urlsafe_b64encode(raw).decode("ascii") != token:
            raise ValueError
        if (
            not isinstance(data, list)
            or len(data) != 5
            or not all(type(part) is str for part in data[1:])
        ):
            raise ValueError
        version, saved, value, key, digest = data
        scope = identity(spec, selection) if scope is None else scope
        if type(version) is not int or version != 2 or saved != scope:
            raise ValueError
        value_raw = bytes.fromhex(value)
        if value_raw.hex() != value or not selection.accepts(
            decode_value(selection.definition, value_raw)
        ):
            raise ValueError
        # Reuse exact root-key and digest validation from equality cursors.
        marker = occurrences.encode_cursor(scope, bytes.fromhex(key), digest.encode("ascii"))
        position = occurrences.decode_cursor(spec, scope, marker)
        assert position is not None
        if position[0].hex() != key:
            raise ValueError
        return value_raw, *position
    except (ValueError, TypeError, UnicodeError, binascii.Error, StorageError, RecursionError):
        raise QueryError("Invalid ordered cursor or cursor from another schema/query") from None


@dataclass
class Budget:
    remaining: int = MAX_VISITS

    def visit(self) -> None:
        if self.remaining == 0:
            raise VisitLimit("Query exceeded its 1000 index-visit budget; narrow the range or page")
        self.remaining -= 1


class VisitLimit(QueryError):
    """Internal distinction allowing a bounded root page to return safe progress."""


def entries(
    store: Store,
    spec: ModelSpec,
    selection: Selection,
    *,
    after: str | None,
    budget: Budget,
    scope: str | None = None,
) -> Iterator[tuple[Any, Path]]:
    definition = selection.definition
    namespace = Path(
        "^YHMFIndex", (spec.global_name[1:].encode("ascii"), definition.name.encode("ascii"))
    )
    if store.get(namespace) is not None:
        raise StorageError("Unexpected value at an ordered-index namespace")
    cursor = decode_cursor(spec, selection, after, scope=scope)
    start, inclusive = selection.start()
    if cursor is not None:
        start, inclusive = cursor[0], True
    present = inclusive and (
        store.get(namespace.child(start)) is not None
        or bool(store.children(namespace.child(start), limit=1))
    )
    reverse = selection.direction == "desc"
    tokens = (
        (start,) if present else child_page(store, namespace, limit=1, after=start, reverse=reverse)
    )
    if not present:
        cursor = None
    while tokens:
        budget.visit()
        token = tokens[0]
        value = decode_value(definition, token)
        if selection.finished(value, token):
            break
        prefix = namespace.child(token)
        position = (cursor[1], cursor[2]) if cursor is not None else None
        while selection.accepts(value) and (
            paths := occurrences.entry_page(store, prefix, limit=1, after=position, reverse=reverse)
        ):
            budget.visit()
            path = paths[0]
            yield value, path
            position = path.subscripts[-2], path.subscripts[-1]
        cursor = None
        tokens = child_page(store, namespace, limit=1, after=token, reverse=reverse)


def page(
    tx: "Database", spec: ModelSpec, selection: Selection, *, limit: int, after: str | None
) -> list[occurrences.Occurrence]:
    spec = tx._check_schema(spec)
    scope = identity(spec, selection)
    result = []
    for value, path in entries(tx.store, spec, selection, after=after, budget=Budget()):
        key, location, record = occurrences.read_indexed(
            tx, spec, selection.definition, indexes.normalized(value), path
        )
        result.append(
            occurrences.Occurrence(
                key,
                location.at,
                selection.definition.name,
                record,
                encode_cursor(scope, path.subscripts[-3], path),
            )
        )
        if len(result) == limit:
            break
    return result
