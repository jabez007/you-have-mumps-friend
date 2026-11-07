# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared record and reference validation while maintenance holds the schema barrier."""

import hashlib

from . import references
from .errors import StorageError
from .models import Model
from .records import metadata, record_nodes
from .registry import ModelSpec, Registry
from .schemas import check_schema
from .storage import Path, Store


def check_metadata(store: Store, spec: ModelSpec, root: Path) -> None:
    if any(store.get(root.child(b"%m", key)) != value for key, value in metadata(spec).items()):
        raise StorageError("A migration record has incompatible or incomplete metadata")


def field_nodes(spec: ModelSpec, root: Path, record: Model) -> dict[Path, bytes]:
    return {
        path: value
        for path, value in record_nodes(spec, root, record).items()
        if path.subscripts[len(root.subscripts)] != b"%m"
    }


def validate_targets(
    store: Store,
    registry: Registry,
    source: ModelSpec,
    target: ModelSpec,
    links: frozenset[references.Link],
) -> None:
    references.replace(store, links, links)
    validate_target_metadata(store, registry, source, target, links)


def validate_target_metadata(
    store: Store,
    registry: Registry,
    source: ModelSpec,
    target: ModelSpec,
    links: frozenset[references.Link],
) -> None:
    """Check proposed targets without requiring their new reverse edges to exist yet."""
    for link in links:
        spec = registry.spec(link.target_model)
        if spec.global_name == target.global_name:
            # A retag batch may encounter an already-retagged self-reference.
            fingerprint = store.get(link.target.child(b"%m", b"fingerprint"))
            spec = target if fingerprint == target.fingerprint else source
        else:
            spec = check_schema(store, spec)
        check_metadata(store, spec, link.target)


def field_digest(root: Path, nodes: dict[Path, bytes]) -> str:
    """Version-one digest: length-delimited relative paths and exact encoded values."""
    digest = hashlib.sha256()
    for path, value in sorted(nodes.items(), key=lambda item: item[0].subscripts):
        parts = path.subscripts[len(root.subscripts) :]
        digest.update(len(parts).to_bytes(1))
        for part in parts:
            digest.update(len(part).to_bytes(4))
            digest.update(part)
        digest.update(len(value).to_bytes(4))
        digest.update(value)
    return digest.hexdigest()
