# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility uses real stored bytes, including saved pre-v0.5 job manifests."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import (
    Database,
    Field,
    Model,
    Path,
    Registry,
    SchemaSnapshot,
    indexes,
    migration_jobs,
)
from mccann_hub.yhmf.errors import SchemaMismatch, StorageError
from mccann_hub.yhmf.records import record_nodes
from mccann_hub.yhmf.registry import _make_spec
from mccann_hub.yhmf.schemas import canonical_json, install_schema, schema_path
from tests.fingerprint_models import Lot, Stock
from tests.migration_models import Product, ProductV2


def seed_legacy(store, record, snapshot=None):
    spec = _make_spec(type(record), legacy=True)
    if snapshot is not None:
        spec = replace(
            spec, snapshot=snapshot.canonical, fingerprint=snapshot.fingerprint.encode("ascii")
        )
    root = spec.path(getattr(record, spec.key_field))

    def install():
        install_schema(store, spec)
        for path, value in record_nodes(spec, root, record).items():
            store.set(path, value)
        indexes.replace(store, frozenset(), indexes.entries(spec, root, record))

    store.atomic(install)
    return spec


def complete(db, status, source, target):
    for _ in range(100):
        if status.phase == "complete":
            return status
        # Reopen on every batch, as an application recovering after interruption.
        db = Database(db.store, Registry(target))
        status = db.advance_migration(status.migration_id, source, target, batch_size=1)
    raise AssertionError("Migration did not finish")


@pytest.mark.parametrize("version", ["2.12.0", "2.14.3"])
def test_legacy_aggregate_and_partial_writes_keep_the_installed_identity(store, version):
    raw = (
        FilePath(__file__).with_name("fixtures") / f"schema-pydantic-{version}.json"
    ).read_bytes()
    snapshot = SchemaSnapshot(raw)
    record = Stock(id="warehouse/雪", price=Decimal("12.50"), lots={"01": Lot(id="01")})
    spec = seed_legacy(store, record, snapshot)
    db = Database(store, Registry(Stock))
    assert db.get(Stock, record.id) == record
    assert db.inspect_schema(Stock).status == "matching"
    db.patch(Stock, record.id, changes={"quantity": Decimal("3.50")}, at=("lots", "01"))
    db.insert_child(
        Stock, record.id, collection=("lots",), record=Lot(id="02", quantity=Decimal(2))
    )
    db.delete_child(Stock, record.id, at=("lots", "01"))
    updated = db.get(Stock, record.id)
    db.save(updated)
    assert db.check_indexes(Stock).issues == ()
    root = schema_path(spec.global_name)
    assert store.get(root) == spec.fingerprint
    assert store.get(root.child(b"snapshot")) == raw
    assert store.get(spec.path(record.id).child(b"%m", b"fingerprint")) == spec.fingerprint
    assert db.delete(Stock, record.id)


@pytest.mark.parametrize("saved_target", ["legacy", "canonical"])
def test_legacy_schema_jobs_resume_the_saved_target_without_record_conversion_on_open(
    store, saved_target
):
    old = seed_legacy(store, Product(id="P", name="Main", sku="S", price=Decimal("12.3400")))
    db = Database(store, Registry(ProductV2))
    if saved_target == "legacy":
        # This is the exact manifest/checkpoint format written by v0.3/v0.4.
        status = migration_jobs.begin(store, old, _make_spec(ProductV2, legacy=True))
    else:
        status = db.begin_migration(Product, ProductV2, plan=db.plan_migration(ProductV2))
    status = complete(db, status, Product, ProductV2)
    assert db.get(ProductV2, "P").price.as_tuple() == Decimal("12.3400").as_tuple()
    assert db.get(ProductV2, "P").quantity == 7
    assert db.check_indexes(ProductV2).issues == ()
    assert db.inspect_schema(ProductV2).installed.format_version == (
        1 if saved_target == "legacy" else 2
    )
    assert db.advance_migration(status.migration_id, Product, ProductV2) == status


def test_legacy_index_rebuild_preserves_records_and_metadata(store):
    spec = seed_legacy(store, Product(id="P", name="Main", sku="S", price=Decimal(1)))
    db = Database(store, Registry(Product))
    status = db.begin_index_rebuild(Product, plan=db.plan_index_rebuild(Product))
    for _ in range(100):
        if status.phase == "complete":
            break
        db = Database(store, Registry(Product))
        status = db.advance_index_rebuild(status.migration_id, Product, batch_size=1)
    assert status.phase == "complete"
    assert db.check_indexes(Product).issues == ()
    assert store.get(schema_path(spec.global_name)) == spec.fingerprint
    assert db.get(Product, "P").sku == "S"


def test_warm_legacy_parsing_cache_cannot_hide_live_metadata_corruption(store):
    spec = seed_legacy(store, Stock(id="W", price=Decimal(1)))
    db = Database(store, Registry(Stock))
    assert db.get(Stock, "W").id == "W"
    root = schema_path(spec.global_name)
    store.set(root.child(b"snapshot"), spec.snapshot + b" ")
    with pytest.raises(StorageError):
        db.get(Stock, "W")
    store.set(root.child(b"snapshot"), spec.snapshot)
    assert db.get(Stock, "W").id == "W"
    document = json.loads(spec.snapshot)
    document["version"] += 1
    different = SchemaSnapshot(canonical_json(document))
    store.set(root.child(b"snapshot"), different.canonical)
    store.set(root, different.fingerprint.encode("ascii"))
    with pytest.raises(SchemaMismatch):
        db.patch(Stock, "W", changes={"price": Decimal(2)})
    assert store.get(spec.path("W").child(b"price")) == b"d:1"


@pytest.mark.parametrize(
    "name,value", [("format_version", 9), ("format_version", True), ("codec_version", 9)]
)
def test_unknown_formats_are_rejected_without_changing_application_data(store, name, value):
    db = Database(store, Registry(Stock))
    db.insert(Stock(id="W", price=Decimal(1)))
    root = schema_path(Stock.__global__)
    document = json.loads(store.get(root.child(b"snapshot")))
    document[name] = value
    store.set(root.child(b"snapshot"), canonical_json(document))
    with pytest.raises(StorageError):
        db.get(Stock, "W")
    assert store.get(Path(Stock.__global__, (b"s:W", b"price"))) == b"d:1"


def test_binary_literal_backfill_uses_the_canonical_codec_default(store):
    class Before(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class After(Before):
        __schema_version__ = 2
        payload: bytes = b"\x00\xff"

    Database(store, Registry(Before)).insert(Before(id="P"))
    db = Database(store, Registry(After))
    status = db.begin_migration(Before, After, plan=db.plan_migration(After))
    complete(db, status, Before, After)
    assert db.get(After, "P").payload == b"\x00\xff"
