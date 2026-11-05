# SPDX-License-Identifier: GPL-3.0-or-later
"""Translate validated record values to native nodes and reconstruct stored fields."""

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
from .models import Model
from .registry import FieldSpec, ModelSpec, NodeSpec
from .storage import MAX_VALUE_BYTES, Path, Store

MAX_CHILDREN = 1000
MAX_RECORD_NODES = 10_000


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
            if field.kind == "scalar" or value is None:
                self.put(path, encode_scalar(cast(Scalar, value)))
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
                values[field.name] = decode_scalar(stored)
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
    return _Reader(store).fields(spec.fields, root)
