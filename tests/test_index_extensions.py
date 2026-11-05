# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest

from mccann_hub.yhmf import Database, Field, Model, Registry, indexes
from mccann_hub.yhmf.errors import StorageError, UniqueViolation


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: int = Field(primary_key=True)
    token: str | None = Field(default=None, index=True)
    amount: Decimal


class Unique(Product):
    token: str | None = Field(default=None, unique=True)
    amount: Decimal = Field(unique=True)


def entries(model, record):
    spec = Registry(model).spec(model)
    return indexes.entries(spec, spec.path(record.id), record)


def test_extension_preserves_existing_entries_and_adds_claims(store, monkeypatch):
    record = Product(id=1, token="one", amount=Decimal("1.00"))
    Database(store, Registry(Product)).insert(record)
    old = entries(Product, record)
    new = entries(Unique, Unique(**record.model_dump()))
    writes = []
    original = store.set

    def capture(path, value):
        writes.append(path)
        original(path, value)

    monkeypatch.setattr(store, "set", capture)
    store.atomic(lambda: indexes.extend(store, old, new))
    assert not {entry.path for entry in old} & set(writes)
    store.atomic(lambda: indexes.check_extension(store, old, new, built=True))
    assert sum(path.global_name == "^YHMFUnique" for path in writes) == 2
    assert sum(path.global_name == "^YHMFIndex" for path in writes) == 1


def test_extension_rejects_demotion_without_mutation(store):
    record = Unique(id=1, token="one", amount=Decimal("1"))
    Database(store, Registry(Unique)).insert(record)
    old = entries(Unique, record)
    new = entries(Product, Product(**record.model_dump()))
    with pytest.raises(StorageError):
        store.atomic(lambda: indexes.extend(store, old, new))
    assert all(store.get(entry.claim_path) == entry.claim for entry in old if entry.claim_path)


def test_extension_collision_rolls_back_every_new_claim_and_entry(store):
    db = Database(store, Registry(Product))
    first = Product(id=1, token="one", amount=Decimal("1.0"))
    second = Product(id=2, token="two", amount=Decimal("1.00"))
    db.insert(first)
    db.insert(second)
    old_first, old_second = entries(Product, first), entries(Product, second)
    new_first = entries(Unique, Unique(**first.model_dump()))
    new_second = entries(Unique, Unique(**second.model_dump()))
    store.atomic(lambda: indexes.extend(store, old_first, new_first))
    with pytest.raises(UniqueViolation):
        store.atomic(lambda: indexes.extend(store, old_second, new_second))
    assert all(
        store.get(entry.path) is None
        for entry in new_second
        if entry.path not in {old.path for old in old_second}
    )
    assert all(
        store.get(entry.claim_path) != entry.claim for entry in new_second if entry.claim_path
    )


def test_null_promotion_creates_no_claim(store):
    record = Product(id=1, amount=Decimal("1"))
    Database(store, Registry(Product)).insert(record)
    old = entries(Product, record)
    new = entries(Unique, Unique(**record.model_dump()))
    extension = store.atomic(lambda: indexes.check_extension(store, old, new, built=False))
    assert len(extension.claims) == 1
    assert next(iter(extension.claims)).claim_path.subscripts[1] == b"amount"
