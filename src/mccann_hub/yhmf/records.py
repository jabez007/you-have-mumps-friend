# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate validated record values to native nodes and reconstruct stored fields."""

from collections.abc import Iterator
from typing import Any, cast

from .codecs import (
    LAYOUT_VERSION,
    Key,
    Scalar,
    decode_key,
    decode_scalar,
    encode_key,
    encode_scalar,
)
from .errors import StorageError
from .models import Model, Reference
from .registry import FieldSpec, ModelSpec, NodeSpec
from .storage import MAX_VALUE_BYTES, Path, Store

type FieldValue = tuple[FieldSpec, tuple[bytes, ...], Any]

MAX_CHILDREN = 1000
MAX_RECORD_NODES = 10_000


def input_values(fields: tuple[FieldSpec, ...], record: Model) -> dict[str, Any]:
    """Force persistence validation from plain owned data without serializers."""
    return {field.name: _input_field(field, getattr(record, field.name)) for field in fields}


def _input_owner(fields: tuple[FieldSpec, ...], value: Any) -> Any:
    if isinstance(value, Model):
        return input_values(fields, value)
    if isinstance(value, dict):
        by_name = {field.name: field for field in fields}
        return {
            name: _input_field(by_name[name], item) if name in by_name else item
            for name, item in value.items()
        }
    return value


def _input_field(field: FieldSpec, value: Any) -> Any:
    if field.kind == "reference" and isinstance(value, Reference):
        return {"key": value.key}
    if field.node is None or value is None:
        return value
    if field.kind == "children" and isinstance(value, dict):
        return {key: _input_owner(field.node.fields, child) for key, child in value.items()}
    return _input_owner(field.node.fields, value)


def metadata(spec: ModelSpec) -> dict[bytes, bytes]:
    return {
        b"layout": str(LAYOUT_VERSION).encode("ascii"),
        b"schema": str(spec.model.__schema_version__).encode("ascii"),
        b"type": spec.global_name[1:].encode("ascii"),
        b"fingerprint": spec.fingerprint,
    }


def _check_child_key(node: NodeSpec, key: Key, value: Any) -> None:
    assert node.key_field is not None
    annotation = node.model.model_fields[node.key_field].annotation
    if type(key) is not annotation or type(value) is not type(key) or key != value:
        raise StorageError("Child dictionary key does not match its declared identity")


def encode_leaf(field: FieldSpec, value: Any) -> bytes:
    if field.choices is not None:
        value = field.choices.primitive(value)
    if field.kind == "scalar" or value is None:
        return encode_scalar(cast(Scalar, value))
    if field.kind == "reference":
        return b"r:" + encode_key(value.key)
    raise StorageError("An owned subtree is not a scalar field")


class _Encoder:
    def __init__(self) -> None:
        self.nodes: dict[Path, bytes] = {}

    def put(self, path: Path, value: bytes) -> None:
        if len(value) > MAX_VALUE_BYTES:
            raise StorageError("Record contains a value exceeding the node limit")
        if len(self.nodes) >= MAX_RECORD_NODES:
            raise StorageError("Record exceeds the node budget")
        self.nodes[path] = value

    def fields(self, fields: tuple[FieldSpec, ...], root: Path, record: Model) -> None:
        for field in fields:
            value = getattr(record, field.name)
            path = root.child(field.name.encode("ascii"))
            if field.kind in {"scalar", "reference"} or value is None:
                self.put(path, encode_leaf(field, value))
            elif field.kind == "embedded":
                assert field.node is not None
                self.put(path, b"o:1")
                self.fields(field.node.fields, path, value)
            else:
                assert field.node is not None
                self.children(field.node, path, value)

    def children(self, node: NodeSpec, path: Path, records: dict[Key, Model]) -> None:
        if len(records) > MAX_CHILDREN:
            raise StorageError("Child collection exceeds 1000 entries")
        self.put(path, b"c:" + str(len(records)).encode("ascii"))
        assert node.key_field is not None
        for key, record in records.items():
            _check_child_key(node, key, getattr(record, node.key_field))
            child_path = path.child(encode_key(key))
            self.put(child_path, b"o:1")
            self.fields(node.fields, child_path, record)


def record_nodes(spec: ModelSpec, root: Path, record: Model) -> dict[Path, bytes]:
    encoder = _Encoder()
    for name, value in metadata(spec).items():
        encoder.put(root.child(b"%m", name), value)
    encoder.fields(spec.fields, root, record)
    return encoder.nodes


def node_nodes(fields: tuple[FieldSpec, ...], root: Path, record: Model) -> dict[Path, bytes]:
    """Encode an owned value without introducing root record metadata."""
    encoder = _Encoder()
    encoder.fields(fields, root, record)
    return encoder.nodes


class _Reader:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.count = 4

    def get(self, path: Path) -> bytes:
        self.count += 1
        if self.count > MAX_RECORD_NODES:
            raise StorageError("Record exceeds the node budget")
        value = self.store.get(path)
        if value is None:
            raise StorageError("A declared field or presence marker is missing from storage")
        return value

    def fields(self, fields: tuple[FieldSpec, ...], root: Path) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in fields:
            path = root.child(field.name.encode("ascii"))
            stored = self.get(path)
            if field.kind == "scalar" or stored == b"n:":
                value = decode_scalar(stored)
                values[field.name] = (
                    field.choices.restore(value) if field.choices is not None else value
                )
            elif field.kind == "reference":
                if not stored.startswith(b"r:"):
                    raise StorageError("Invalid reference encoding")
                assert field.target is not None
                assert field.target_key is not None
                annotation = field.target.model_fields[field.target_key].annotation
                key = decode_key(stored[2:])
                if type(key) is not annotation:
                    raise StorageError("Reference key type does not match its target")
                values[field.name] = {"key": key}
            elif field.kind == "embedded":
                assert field.node is not None
                if stored != b"o:1":
                    raise StorageError("Invalid embedded object marker")
                values[field.name] = self.fields(field.node.fields, path)
            else:
                assert field.node is not None
                values[field.name] = self.children(field.node, path, stored)
        return values

    def children(self, node: NodeSpec, path: Path, marker: bytes) -> dict[Key, Any]:
        keys = self.store.children(path, limit=MAX_CHILDREN + 1)
        if len(keys) > MAX_CHILDREN or marker != b"c:" + str(len(keys)).encode("ascii"):
            raise StorageError("Invalid or oversized child collection")
        records: dict[Key, Any] = {}
        assert node.key_field is not None
        for raw_key in keys:
            key = decode_key(raw_key)
            child_path = path.child(raw_key)
            if self.get(child_path) != b"o:1":
                raise StorageError("Invalid child presence marker")
            values = self.fields(node.fields, child_path)
            _check_child_key(node, key, values[node.key_field])
            records[key] = values
        return records


def record_values(spec: ModelSpec, root: Path, store: Store) -> dict[str, Any]:
    return node_values(spec.fields, root, store)


def node_values(fields: tuple[FieldSpec, ...], root: Path, store: Store) -> dict[str, Any]:
    return _Reader(store).fields(fields, root)


def walk_values(
    fields: tuple[FieldSpec, ...],
    record: Model,
    prefix: tuple[bytes, ...] = (),
) -> Iterator[tuple[FieldSpec, tuple[bytes, ...], Any]]:
    """Visit declared scalar and reference values with their complete owned paths."""
    for field in fields:
        value = getattr(record, field.name)
        path = prefix + (field.name.encode("ascii"),)
        if field.node is None:
            yield field, path, value
        elif value is not None and field.kind == "embedded":
            yield from walk_values(field.node.fields, value, path)
        elif value is not None:
            for key, child in value.items():
                yield from walk_values(field.node.fields, child, path + (encode_key(key),))
