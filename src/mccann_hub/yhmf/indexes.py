# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed equality entries and transactional uniqueness claims for owned values."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from .codecs import Scalar, encode_key, encode_scalar
from .errors import SchemaError, StorageError, UniqueViolation
from .models import Model, Reference
from .records import FieldValue, walk_values
from .registry import FieldSpec, ModelSpec
from .storage import Path, Store


@dataclass(frozen=True)
class Definition:
    path: tuple[str, ...]
    field: FieldSpec
    owner: type[Model]

    @property
    def name(self) -> str:
        return ".".join(self.path)

    def validate(self, value: Any) -> Any:
        assert self.field.adapter is not None
        result = self.field.adapter.validate_python(value)
        if self.field.choices is not None:
            self.field.choices.primitive(result)
        if result is not None and self.field.target is not None:
            assert self.field.target_key is not None
            key_type = self.field.target.model_fields[self.field.target_key].annotation
            if type(result.key) is not key_type:
                raise SchemaError("Reference query key type does not match its target")
            encode_key(result.key)
        return result


def _definitions(
    model: type[Model],
    fields: tuple[FieldSpec, ...],
    prefix: tuple[str, ...],
) -> Iterator[Definition]:
    for field in fields:
        path = prefix + (field.name,)
        if field.index:
            yield Definition(path, field, model)
        if field.node is not None:
            yield from _definitions(field.node.model, field.node.fields, path)


def definitions(spec: ModelSpec) -> tuple[Definition, ...]:
    return tuple(_definitions(spec.model, spec.fields, ()))


def _decimal_value(value: Decimal) -> bytes:
    if not value.is_finite():
        raise StorageError("Index values must be finite")
    sign, digits, exponent = value.as_tuple()
    text = "".join(str(digit) for digit in digits).rstrip("0")
    if not text:
        return b"d:0"
    power = cast(int, exponent) + len(digits) - len(text)
    prefix = "-" if sign else ""
    return f"d:{prefix}{text}E{power}".encode("ascii")


def normalized(value: Any) -> bytes:
    """Normalize equality without rounding decimals or changing stored values."""
    if isinstance(value, Decimal):
        return _decimal_value(value)
    if isinstance(value, Reference):
        return b"r:" + encode_key(value.key)
    if type(value) is float and value == 0:
        return b"f:0.0"
    return encode_scalar(cast(Scalar, value))


def value_key(value: Any) -> bytes:
    return b"v:" + json.dumps(normalized(value).decode("utf-8"), ensure_ascii=True).encode("ascii")


def index_root(spec: ModelSpec, definition: Definition, value: Any) -> Path:
    return Path(
        "^YHMFIndex",
        (spec.global_name[1:].encode("ascii"), definition.name.encode("ascii"), value_key(value)),
    )


@dataclass(frozen=True)
class Entry:
    path: Path
    location: bytes
    claim_path: Path | None
    claim: bytes


def entries(spec: ModelSpec, root: Path, record: Model) -> frozenset[Entry]:
    return entries_at(spec, root, walk_values(spec.fields, record))


def entries_at(spec: ModelSpec, root: Path, values: Iterable[FieldValue]) -> frozenset[Entry]:
    """Build normal index identities for explicitly addressed field occurrences."""
    by_field = {id(definition.field): definition for definition in definitions(spec)}
    result = set()
    for field, path, value in values:
        definition = by_field.get(id(field))
        if definition is None:
            continue
        index = index_root(spec, definition, value)
        location = json.dumps(
            [part.decode("utf-8") for part in path], ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
        digest = hashlib.sha256(location).hexdigest().encode("ascii")
        entry_path = index.child(*root.subscripts, digest)
        claim_path = (
            Path("^YHMFUnique", index.subscripts) if field.unique and value is not None else None
        )
        claim = json.dumps(
            [root.subscripts[0].decode("utf-8"), json.loads(location)],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        result.add(Entry(entry_path, location, claim_path, claim))
    return frozenset(result)


def _validate_old(store: Store, old: frozenset[Entry]) -> None:
    for entry in old:
        if store.get(entry.path) != entry.location:
            raise StorageError("A secondary index entry is missing or corrupt")
        if entry.claim_path is not None and store.get(entry.claim_path) != entry.claim:
            raise StorageError("A unique index claim is missing or corrupt")


@dataclass(frozen=True)
class Extension:
    """New equality entries and claims, preserving every existing identity."""

    entries: frozenset[Entry]
    claims: frozenset[Entry]


def _extension(old: frozenset[Entry], new: frozenset[Entry]) -> Extension:
    by_path = {entry.path: entry for entry in new}
    for entry in old:
        target = by_path.get(entry.path)
        if target is None or (target.location, target.claim) != (entry.location, entry.claim):
            raise StorageError("Index extension would change an existing equality identity")
        if entry.claim_path is not None and entry.claim_path != target.claim_path:
            raise StorageError("Index extension would remove an existing unique claim")
    old_paths = {entry.path for entry in old}
    old_claims = {entry.claim_path for entry in old}
    return Extension(
        frozenset(entry for entry in new if entry.path not in old_paths),
        frozenset(entry for entry in new if entry.claim_path not in old_claims | {None}),
    )


def check_extension(
    store: Store, old: frozenset[Entry], new: frozenset[Entry], *, built: bool
) -> Extension:
    """Validate an additive transition and its committed or not-yet-built nodes."""
    extension = _extension(old, new)
    _validate_old(store, old)
    if built:
        _validate_old(store, new)
    else:
        for entry in extension.entries:
            if store.get(entry.path) is not None:
                raise StorageError("An unexpected secondary index entry already exists")
        for entry in extension.claims:
            assert entry.claim_path is not None
            if store.get(entry.claim_path) is not None:
                raise UniqueViolation("An unexpected unique index claim already exists")
    return extension


def extend(store: Store, old: frozenset[Entry], new: frozenset[Entry]) -> None:
    """Add equality entries or promote them to unique, inside the caller's transaction."""
    extension = check_extension(store, old, new, built=False)
    for entry in extension.claims:
        assert entry.claim_path is not None
        store.set(entry.claim_path, entry.claim)
    for entry in extension.entries:
        store.set(entry.path, entry.location)


def replace(store: Store, old: frozenset[Entry], new: frozenset[Entry]) -> None:
    _validate_old(store, old)
    for entry in old - new:
        store.delete_tree(entry.path)
        if entry.claim_path is not None:
            store.delete_tree(entry.claim_path)
    for entry in new - old:
        if store.get(entry.path) is not None:
            raise StorageError("An unexpected secondary index entry already exists")
        if entry.claim_path is not None:
            if store.get(entry.claim_path) is not None:
                raise UniqueViolation("A unique index value is already claimed")
            store.set(entry.claim_path, entry.claim)
        store.set(entry.path, entry.location)


@dataclass(frozen=True)
class IndexTransition:
    """Exact native nodes added, removed, or retained by a schema transition."""

    additions: dict[Path, bytes]
    removals: dict[Path, bytes]
    retained: dict[Path, bytes]
    claims: frozenset[Entry]


def _entry_nodes(entries: frozenset[Entry]) -> dict[Path, bytes]:
    nodes: dict[Path, bytes] = {}
    for entry in entries:
        expected = {entry.path: entry.location}
        if entry.claim_path is not None:
            expected[entry.claim_path] = entry.claim
        for path, value in expected.items():
            if path in nodes and nodes[path] != value:
                raise UniqueViolation("Conflicting owners prevent index migration")
            nodes[path] = value
    return nodes


def check_transition(
    store: Store, old: frozenset[Entry], new: frozenset[Entry], *, updated: bool
) -> IndexTransition:
    """Validate both sides of a migration without changing retained identities."""
    before, after = _entry_nodes(old), _entry_nodes(new)
    shared = before.keys() & after.keys()
    if any(before[path] != after[path] for path in shared):
        raise StorageError("Index migration would change a retained node identity")
    additions = {path: value for path, value in after.items() if path not in before}
    removals = {path: value for path, value in before.items() if path not in after}
    _check_transition_nodes(store, after if updated else before, removals if updated else additions)
    if not updated and any(store.children(path, limit=1) for path in removals):
        raise StorageError("An expected index leaf contains an unexpected subtree")
    return IndexTransition(
        additions,
        removals,
        {path: before[path] for path in shared},
        frozenset(entry for entry in new if entry.claim_path in additions),
    )


def _check_transition_nodes(
    store: Store, expected: dict[Path, bytes], absent: dict[Path, bytes]
) -> None:
    for path, value in expected.items():
        if store.get(path) != value:
            raise StorageError("An expected index entry or claim is missing or corrupt")
    for path in absent:
        if store.get(path) is not None or store.children(path, limit=1):
            raise StorageError("An index entry or claim is not absent as expected")


def apply_transition(store: Store, old: frozenset[Entry], new: frozenset[Entry]) -> None:
    """Update only changed leaves inside the caller's migration transaction."""
    transition = check_transition(store, old, new, updated=False)
    for path in transition.removals:
        store.delete_tree(path)
    for path, value in transition.additions.items():
        store.set(path, value)
