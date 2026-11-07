# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, explicit adoption of otherwise unmanaged layout-one records."""

from typing import TYPE_CHECKING

from . import indexes, references
from .codecs import decode_key
from .errors import StorageError
from .models import Model
from .records import MAX_RECORD_NODES, metadata, record_nodes, record_values, validate_stored
from .registry import ModelSpec
from .schemas import has_data, install_schema, schema_path
from .storage import Path, Store, subtree_nodes

if TYPE_CHECKING:
    from .repository import Database

MAX_ADOPTION_RECORDS = 100


def require_unmanaged(store: Store, spec: ModelSpec) -> None:
    """Require absent managed metadata/secondary structures and no root value."""
    if has_data(store, schema_path(spec.global_name)):
        raise StorageError("Adoption requires completely absent installed schema metadata")
    root = Path(spec.global_name)
    if store.get(root) is not None:
        raise StorageError("Adoption cannot interpret a value at the application global root")
    for name in ("^YHMFIndex", "^YHMFUnique", "^YHMFRef"):
        if has_data(store, Path(name, (spec.global_name[1:].encode("ascii"),))):
            raise StorageError("Adoption requires absent index and inbound-reference namespaces")


def prepare(store: Store, spec: ModelSpec) -> tuple[tuple[Path, Model], ...]:
    """Validate every byte before metadata or secondary structures are installed."""
    require_unmanaged(store, spec)
    root = Path(spec.global_name)
    keys = store.children(root, limit=MAX_ADOPTION_RECORDS + 1)
    if len(keys) > MAX_ADOPTION_RECORDS:
        raise StorageError("Adoption supports at most 100 records per global")
    result = []
    budget = 0
    for key in keys:
        path, value, count = read_record(store, spec, key)
        budget += count
        if budget > MAX_RECORD_NODES:
            raise StorageError("Adoption exceeds its total node budget")
        result.append((path, value))
    return tuple(result)


def read_record(store: Store, spec: ModelSpec, key: bytes) -> tuple[Path, Model, int]:
    path = spec.path(decode_key(key))
    record = validate_stored(spec.fields, spec.model, record_values(spec, path, store))
    if spec.path(getattr(record, spec.key_field)) != path:
        raise StorageError("Adopted record identity does not match its path")
    expected = record_nodes(spec, path, record)
    fields = {p: v for p, v in expected.items() if p.subscripts[1] != b"%m"}
    actual = subtree_nodes(store, path, limit=MAX_RECORD_NODES)
    if actual != fields and actual != expected:
        raise StorageError(
            "Adoption requires exact layout-one bytes with absent or intact metadata"
        )
    return path, record, len(expected)


def install_record_metadata(store: Store, spec: ModelSpec, root: Path) -> None:
    for name, value in metadata(spec).items():
        store.set(root.child(b"%m", name), value)


def adopt(tx: "Database", spec: ModelSpec) -> int:
    records = prepare(tx.store, spec)
    install_schema(tx.store, spec)
    for root, _ in records:
        install_record_metadata(tx.store, spec, root)
    links = frozenset(
        link
        for root, record in records
        for link in references.links(tx.registry, spec, root, record)
    )
    tx._check_reference_targets(links)
    references.replace(tx.store, frozenset(), links)
    # Install each record separately so duplicate unique values cannot collapse
    # into one set entry and escape the normal uniqueness check.
    for root, record in records:
        indexes.replace(tx.store, frozenset(), indexes.entries(spec, root, record))
    return len(records)
