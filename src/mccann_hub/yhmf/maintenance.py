# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable migration ownership and checkpoints, independent of model bindings."""

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .codecs import canonical_json, decode_key
from .errors import MaintenanceActive, MigrationError, StorageError
from .storage import Path, Store

GATE = Path("^YHMFMigration", (b"gate",))
PHASES = ("validate", "backfill", "verify", "retag", "cleanup", "activate", "complete", "aborted")


def job_path(migration_id: str) -> Path:
    if not isinstance(migration_id, str) or not re.fullmatch(r"[0-9a-f]{32}", migration_id):
        raise MigrationError("Invalid migration identifier")
    return Path("^YHMFMigration", (b"jobs", migration_id.encode("ascii")))


def marker(migration_id: str) -> bytes:
    job_path(migration_id)
    return b"migration:" + migration_id.encode("ascii")


def gate_owner(store: Store) -> str | None:
    raw = store.get(GATE)
    if raw is None:
        return None
    try:
        owner = raw.decode("ascii")
        job_path(owner)
    except (UnicodeError, MigrationError) as exc:
        raise StorageError("Malformed maintenance gate") from exc
    return owner


def require_available(store: Store) -> None:
    if gate_owner(store) is not None:
        raise MaintenanceActive("A migration holds the mapper maintenance gate")


@dataclass(frozen=True)
class MigrationStatus:
    """A committed checkpoint. Record keys are omitted from ordinary diagnostics."""

    migration_id: str
    global_name: str
    source_fingerprint: str
    target_fingerprint: str
    phase: str = "validate"
    validated: int = 0
    backfilled: int = 0
    verified: int = 0
    retagged: int = 0
    cursor: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        job_path(self.migration_id)
        Path(self.global_name)
        for fingerprint in (self.source_fingerprint, self.target_fingerprint):
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise StorageError("Invalid migration fingerprint")
        if self.phase not in PHASES:
            raise StorageError("Invalid migration phase")
        counts = (self.validated, self.backfilled, self.verified, self.retagged)
        if any(type(value) is not int or value < 0 for value in counts):
            raise StorageError("Invalid migration counters")
        if list(counts) != sorted(counts, reverse=True):
            raise StorageError("Migration counters are out of order")
        if not isinstance(self.cursor, bytes):
            raise StorageError("Invalid migration cursor")
        if self.cursor:
            decode_key(self.cursor)

    def to_dict(self, *, include_cursor: bool = False) -> dict[str, Any]:
        result = asdict(self)
        result.pop("cursor")
        if include_cursor:
            result["cursor"] = self.cursor.hex()
        return result


def save_status(store: Store, status: MigrationStatus) -> None:
    document = status.to_dict(include_cursor=True)
    document["format_version"] = 1
    store.set(job_path(status.migration_id), canonical_json(document))


def load_status(store: Store, migration_id: str) -> MigrationStatus:
    raw = store.get(job_path(migration_id))
    if raw is None:
        raise MigrationError("Migration job does not exist")
    try:
        document = json.loads(raw)
        if canonical_json(document) != raw:
            raise ValueError("Invalid checkpoint encoding")
        version = document.pop("format_version")
        if type(version) is not int or version != 1:
            raise ValueError("Unsupported checkpoint format")
        document["cursor"] = bytes.fromhex(document["cursor"])
        status = MigrationStatus(**document)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, MigrationError) as exc:
        raise StorageError("Malformed migration checkpoint") from exc
    if status.migration_id != migration_id:
        raise StorageError("Migration checkpoint identity does not match its location")
    return status


def require_owner(store: Store, status: MigrationStatus) -> None:
    if gate_owner(store) != status.migration_id:
        raise MigrationError("Migration does not own the maintenance gate")
    root = Path("^YHMFSchema", (status.global_name[1:].encode("ascii"),))
    if store.get(root) != marker(status.migration_id):
        raise StorageError("Migration schema barrier is missing or corrupt")


def inspected_fingerprint(
    store: Store, global_name: str, fingerprint: bytes | None
) -> tuple[bytes | None, str | None]:
    """Expose the source schema for inspection while ordinary access stays blocked."""
    if fingerprint is None or not fingerprint.startswith(b"migration:"):
        return fingerprint, None
    owner = gate_owner(store)
    if owner is None or marker(owner) != fingerprint:
        raise StorageError("Schema barrier has no matching maintenance owner")
    status = load_status(store, owner)
    if status.global_name != global_name or status.phase in {"complete", "aborted"}:
        raise StorageError("Schema barrier does not match an active migration")
    return status.source_fingerprint.encode("ascii"), owner
