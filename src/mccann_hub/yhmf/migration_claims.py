# SPDX-License-Identifier: GPL-3.0-or-later
"""Job-scoped uniqueness reservations, separate from active application claims."""

import hashlib
import json

from .codecs import canonical_json
from .errors import StorageError, UniqueViolation
from .indexes import Entry
from .maintenance import job_path
from .storage import Path, Store


def _reservation(migration_id: str, entry: Entry) -> tuple[Path, bytes]:
    assert entry.claim_path is not None
    identity = [part.decode("ascii") for part in entry.claim_path.subscripts]
    digest = hashlib.sha256(canonical_json(identity)).hexdigest().encode("ascii")
    path = job_path(migration_id).child(b"unique", digest)
    # Keep the complete identity as well as the digest, so collisions fail closed.
    payload = canonical_json({"index": identity, "owner": json.loads(entry.claim)})
    return path, payload


def reserve(store: Store, migration_id: str, claims: frozenset[Entry]) -> None:
    """Detect duplicates across committed validation batches without active claims."""
    for entry in claims:
        path, payload = _reservation(migration_id, entry)
        current = store.get(path)
        if current is not None and current != payload:
            raise UniqueViolation("Duplicate values prevent unique index migration")
        if current is None:
            store.set(path, payload)


def verify(store: Store, migration_id: str, claims: frozenset[Entry]) -> None:
    """Require the validated value and owner before building or verifying claims."""
    for entry in claims:
        path, payload = _reservation(migration_id, entry)
        if store.get(path) != payload:
            raise StorageError("A migration uniqueness reservation is missing or corrupt")
