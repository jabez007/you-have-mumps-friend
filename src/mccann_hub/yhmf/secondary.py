# SPDX-License-Identifier: GPL-3.0-or-later
"""One transactional reference/index transition for explicit field changes."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import indexes, references
from .errors import ReferenceViolation
from .records import FieldValue
from .registry import ModelSpec
from .storage import Path

if TYPE_CHECKING:
    from .repository import Database


@dataclass(frozen=True)
class Change:
    spec: ModelSpec
    root: Path
    before: tuple[FieldValue, ...]
    after: tuple[FieldValue, ...]


def replace(
    tx: "Database", changes: tuple[Change, ...], *, deleting_roots: frozenset[Path] = frozenset()
) -> None:
    old_links = frozenset(
        link
        for change in changes
        for link in references.links_at(tx.registry, change.spec, change.root, change.before)
    )
    new_links = frozenset(
        link
        for change in changes
        for link in references.links_at(tx.registry, change.spec, change.root, change.after)
    )
    old_entries = frozenset(
        entry
        for change in changes
        for entry in indexes.entries_at(change.spec, change.root, change.before)
    )
    new_entries = frozenset(
        entry
        for change in changes
        for entry in indexes.entries_at(change.spec, change.root, change.after)
    )
    if any(link.target in deleting_roots for link in new_links):
        raise ReferenceViolation("A surviving validator retained a reference to a deleted target")
    tx._check_reference_targets(new_links)
    references.replace(tx.store, old_links, new_links)
    for root in deleting_roots:
        references.restrict_delete(tx.store, root)
    indexes.replace(tx.store, old_entries, new_entries)
