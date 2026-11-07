# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit bounded groups, using ordinary patch validation and secondary transitions."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import indexes, mutations, references
from .codecs import Key
from .errors import MutationError
from .models import Model
from .owned import OwnedPath
from .record_budget import require_growth

if TYPE_CHECKING:
    from .repository import Database


@dataclass(frozen=True)
class Patch:
    """One owner change; patch_many snapshots input values before validation/retries."""

    model: type[Model]
    key: Key = field(repr=False)
    changes: Mapping[str, Any] = field(kw_only=True, repr=False)
    at: OwnedPath = field(default=(), kw_only=True, repr=False)


def snapshot(items: Sequence[Patch]) -> tuple[Patch, ...]:
    if not isinstance(items, Sequence) or not 1 <= len(items) <= 100:
        raise MutationError("patch_many requires a sequence of 1 to 100 Patch values")
    if any(not isinstance(item, Patch) for item in items):
        raise MutationError("patch_many requires Patch values")
    return tuple(
        Patch(item.model, item.key, at=deepcopy(item.at), changes=deepcopy(dict(item.changes)))
        for item in items
    )


def apply(tx: "Database", items: tuple[Patch, ...]) -> None:
    prepared = tuple(
        mutations.prepare_patch(tx, tx.registry.spec(item.model), item.key, item.at, item.changes)
        for item in items
    )
    if len({item.owner for item in prepared}) != len(prepared):
        raise MutationError("Combine changes for the same owner in one Patch")
    require_growth(tx.store, ((item.spec, item.root, item.growth) for item in prepared))
    # Unlike a single owner transition, a group releases the union of old claims
    # before claiming any new values. The existing replace helpers enforce the
    # same integrity rules, including conflicts within the new group itself.
    old_links = frozenset(
        link
        for item in prepared
        for link in references.links_at(tx.registry, item.spec, item.root, item.before)
    )
    new_links = frozenset(
        link
        for item in prepared
        for link in references.links_at(tx.registry, item.spec, item.root, item.after)
    )
    old_entries = frozenset(
        entry
        for item in prepared
        for entry in indexes.entries_at(item.spec, item.root, item.before)
    )
    new_entries = frozenset(
        entry for item in prepared for entry in indexes.entries_at(item.spec, item.root, item.after)
    )
    tx._check_reference_targets(new_links)
    references.replace(tx.store, old_links, new_links)
    indexes.replace(tx.store, old_entries, new_entries)
    for item in prepared:
        item.apply_nodes(tx.store)
