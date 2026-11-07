# SPDX-License-Identifier: GPL-3.0-or-later
"""Reverse-reference identities and transactional inbound counts."""

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import ReferenceViolation, StorageError
from .models import Model
from .records import FieldValue, walk_values
from .registry import ModelSpec, Registry
from .storage import Path, Store


def reference_root(target: Path) -> Path:
    return Path("^YHMFRef", (target.global_name[1:].encode("ascii"), *target.subscripts))


@dataclass(frozen=True)
class Link:
    target_model: type[Model]
    target: Path
    index_path: Path
    location: bytes


def links(registry: Registry, spec: ModelSpec, root: Path, record: Model) -> frozenset[Link]:
    return links_at(registry, spec, root, walk_values(spec.fields, record))


def links_at(
    registry: Registry, spec: ModelSpec, root: Path, values: Iterable[FieldValue]
) -> frozenset[Link]:
    """Build normal reverse identities for explicitly addressed field occurrences."""
    result = set()
    for field, path, value in values:
        if field.target is None or value is None:
            continue
        target = registry.spec(field.target).path(value.key)
        location = json.dumps(
            [part.decode("utf-8") for part in path], ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
        digest = hashlib.sha256(location).hexdigest().encode("ascii")
        index = reference_root(target).child(
            root.global_name[1:].encode("ascii"), *root.subscripts, digest
        )
        result.add(Link(field.target, target, index, location))
    return frozenset(result)


def inbound_count(store: Store, root: Path) -> int:
    """Read the canonical count and check edge presence without enumerating sources."""
    value = store.get(root)
    if value is None:
        count = 0
    else:
        try:
            count = int(value)
        except ValueError as exc:
            raise StorageError("Invalid reverse-reference count") from exc
        if count < 0 or str(count).encode("ascii") != value:
            raise StorageError("Invalid reverse-reference count")
    if bool(store.children(root, limit=1)) != bool(count):
        raise StorageError("Reverse-reference count and entries disagree")
    return count


def _validate_entries(store: Store, old: frozenset[Link], added: frozenset[Link]) -> None:
    for link in old:
        if store.get(link.index_path) != link.location:
            raise StorageError("A reverse-reference entry is missing or corrupt")
    for link in added:
        if store.get(link.index_path) is not None:
            raise StorageError("An unexpected reverse-reference entry already exists")


def replace(store: Store, old: frozenset[Link], new: frozenset[Link]) -> None:
    """Maintain exact edges and a direct count node used by deletion transactions."""
    removed, added = old - new, new - old
    _validate_entries(store, old, added)
    deltas: Counter[Path] = Counter()
    for link in added:
        deltas[reference_root(link.target)] += 1
    for link in removed:
        deltas[reference_root(link.target)] -= 1
    counts = {root: inbound_count(store, root) + delta for root, delta in deltas.items()}
    if any(count < 0 for count in counts.values()):
        raise StorageError("Reverse-reference count would become negative")
    for link in removed:
        store.delete_tree(link.index_path)
    for link in added:
        store.set(link.index_path, link.location)
    for root, count in counts.items():
        store.set(root, str(count).encode("ascii"))


def restrict_delete(store: Store, target: Path) -> None:
    root = reference_root(target)
    if inbound_count(store, root):
        raise ReferenceViolation(
            "Record is still referenced; remove or change those references first"
        )
    store.delete_tree(root)
