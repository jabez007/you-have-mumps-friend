# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
from decimal import Decimal

import pytest

from mccann_hub.yhmf import Database, Field, Model, Registry, indexes
from mccann_hub.yhmf.errors import StorageError, UniqueViolation


class Source(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    amount: Decimal = Field(unique=True)
    category: str = Field(index=True)
    locked: str = Field(unique=True)


class Target(Source):
    sku: str | None = Field(default=None, index=True)
    amount: Decimal
    category: str


def seeded(store, sku="one"):
    record = Source(id=1, sku=sku, amount=Decimal("1.00"), category="stock", locked="keep")
    Database(store, Registry(Source)).insert(record)
    old_spec, new_spec = Registry(Source).spec(Source), Registry(Target).spec(Target)
    root = old_spec.path(1)
    old = indexes.entries(old_spec, root, record)
    new = indexes.entries(new_spec, root, Target(**record.model_dump()))
    return old, new


@pytest.mark.parametrize("sku", ["one", None])
def test_transition_removes_only_changed_claims_and_entries(store, monkeypatch, sku):
    old, new = seeded(store, sku)
    transition = indexes.check_transition(store, old, new, updated=False)
    changes = []
    original_delete, original_set = store.delete_tree, store.set

    def capture_delete(path):
        assert store.children(path, limit=1) == ()
        changes.append(path)
        original_delete(path)

    def capture_set(path, value):
        changes.append(path)
        original_set(path, value)

    monkeypatch.setattr(store, "delete_tree", capture_delete)
    monkeypatch.setattr(store, "set", capture_set)
    store.atomic(lambda: indexes.apply_transition(store, old, new))
    assert not set(changes) & transition.retained.keys()
    assert set(changes) == transition.removals.keys()
    assert not transition.additions and not transition.claims
    indexes.check_transition(store, old, new, updated=True)


def test_missing_source_claim_prevents_any_transition(store):
    old, new = seeded(store)
    claim = next(entry.claim_path for entry in old if entry.claim_path)
    store.delete_tree(claim)
    with pytest.raises(StorageError):
        store.atomic(lambda: indexes.apply_transition(store, old, new))
    assert all(store.get(entry.path) == entry.location for entry in old)


def test_transition_refuses_to_delete_an_unexpected_subtree(store):
    old, new = seeded(store)
    transition = indexes.check_transition(store, old, new, updated=False)
    path = next(iter(transition.removals))
    store.set(path.child(b"raw"), b"unmanaged")
    with pytest.raises(StorageError, match="subtree"):
        store.atomic(lambda: indexes.apply_transition(store, old, new))
    assert all(store.get(entry.path) == entry.location for entry in old)


def test_retained_identity_changes_are_rejected(store):
    old, _ = seeded(store)
    entry = next(iter(old))
    changed = (old - {entry}) | {replace(entry, location=b"changed")}
    with pytest.raises(StorageError, match="retained"):
        indexes.check_transition(store, old, changed, updated=False)


def test_conflicting_owners_cannot_be_hidden_by_node_deduplication(store):
    old, _ = seeded(store)
    entry = next(entry for entry in old if entry.claim_path)
    conflicting = replace(entry, claim=b"another owner")
    with pytest.raises(UniqueViolation):
        indexes.check_transition(store, old, old | {conflicting}, updated=False)
