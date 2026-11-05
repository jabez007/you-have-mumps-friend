# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed equality entries and transactional uniqueness claims for owned values."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from pydantic import TypeAdapter

from .codecs import Scalar, encode_key, encode_scalar
from .errors import SchemaError, StorageError, UniqueViolation
from .models import Model, Reference
from .records import walk_values
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
        annotation = self.owner.model_fields[self.field.name].rebuild_annotation()
        result = TypeAdapter(annotation).validate_python(value, strict=True)
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
    by_field = {id(definition.field): definition for definition in definitions(spec)}
    result = set()
    for field, path, value in walk_values(spec.fields, record):
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
