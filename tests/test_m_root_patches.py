# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit M root/non-leaf patches preserve bounded hierarchical updates."""

import pytest

from examples.inventory import MODELS, Location, Lot, Product, Warehouse
from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Path, Registry
from tests import test_m_owned_writers, test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_m_writers import MRecord

m_writer = test_m_writers.m_writer
owned_writer = test_m_owned_writers.owned_writer
variant_writer = test_m_owned_writers.variant_writer


def test_export_has_explicit_root_patch_and_participant_interfaces(store):
    for model, registry in ((MRecord, Registry(MRecord)), (Warehouse, Registry(*MODELS))):
        source = Database(store, registry).m_routine(model, name="PatchAPI")
        assert "patch(key,input)\n" in source
        assert "patchtx(key,input)\n" in source


@pytest.mark.native
def test_flat_root_patch_matches_python_and_rolls_back_unique_failure(store, m_writer):
    db, run, _, _ = m_writer
    db.insert(MRecord(id="A", sku="S", name="Stock", quantity=1))
    db.insert(MRecord(id="B", sku="T", name="Other", quantity=1))
    expected = Database(MemoryStore(), Registry(MRecord))
    expected.insert(MRecord(id="A", sku="S", name="Stock", quantity=1))
    expected.insert(MRecord(id="B", sku="T", name="Other", quantity=1))
    roots = (MRecord.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert run("rootpatch", {"quantity": "2"}) == "ok"
    expected.patch(MRecord, "A", changes={"quantity": 2})
    assert dump(store, roots) == dump(expected.store, roots)
    before = dump(store, roots)
    assert run("rootpatch", {"sku": "T", "quantity": "3"}) == "unique"
    assert run("rootpatch", {"id": "B"}) == "invalid"
    assert run("rootpatch", {}) == "invalid"
    assert run("rootpatch", {"_key": "MISSING", "quantity": "2"}) == "missing"
    assert dump(store, roots) == before
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
def test_hierarchical_root_patch_preserves_all_children_and_matches_python(store, owned_writer):
    db, run, _, _ = owned_writer
    roots = test_m_owned_writers.namespaces()
    expected_store = MemoryStore()
    for path, value in dump(store, roots).items():
        expected_store.set(path, value)
    expected = Database(expected_store, Registry(*MODELS))
    assert run("rootpatch", {"_key": "MAIN", "name": "Renamed warehouse"}) == "ok"
    expected.patch(Warehouse, "MAIN", changes={"name": "Renamed warehouse"})
    assert dump(store, roots) == dump(expected_store, roots)
    before = dump(store, roots)
    assert run("rootpatch", {"_key": "MAIN", "stored_on_hand": "1"}) == "invalid"
    assert run("rootpatch", {"_key": "MAIN", "locations": "replace"}) == "invalid"
    assert dump(store, roots) == before
    assert db.get(Warehouse, "MAIN").name == "Renamed warehouse"
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("damage", ["missing", "tag", "count", "extra"])
def test_root_patch_rejects_invalid_immediate_collection_shape(store, owned_writer, damage):
    _, run, _, _ = owned_writer
    root = Path(Warehouse.__global__, (b"s:MAIN",))
    if damage == "missing":
        store.delete_tree(root.child(b"locations"))
    elif damage == "extra":
        store.set(root.child(b"unknown"), b"s:x")
    else:
        store.set(root.child(b"locations"), b"s:2" if damage == "tag" else b"c:0")
    roots = test_m_owned_writers.namespaces()
    before = dump(store, roots)
    assert run("rootpatch", {"_key": "MAIN", "name": "Changed"}) == "corrupt"
    assert dump(store, roots) == before


@pytest.mark.native
def test_root_patch_participants_restart_and_rollback_prior_changes(store, m_writer):
    db, run, routine, _ = m_writer
    db.insert(MRecord(id="A", sku="S", name="Stock", quantity=1))
    db.insert(MRecord(id="B", sku="T", name="Other", quantity=1))
    roots = (MRecord.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    before = dump(store, roots)
    assert run("rootpair", {"sku": "S"}) == "unique|0"
    assert dump(store, roots) == before
    source = db.m_routine(MRecord, name="YHMFTAPI")
    marker = " ; YHMF reference changes complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    assert run("rootpair", {"sku": "NEW"}) == "ok|0"
    assert db.get(MRecord, "A").quantity == 2
    assert db.get(MRecord, "B").sku == "NEW"
    assert db.check_indexes(MRecord).issues == ()


class PatchableLocation(Location):
    label: str = Field(default="zone", index=True, min_length=1)


class PatchableWarehouse(Warehouse):
    __schema_version__ = 4
    locations: Children[PatchableLocation] = ChildField(key="id")


@pytest.mark.native
def test_nonleaf_owner_patch_preserves_descendants_and_unrelated_indexes(store, variant_writer):
    db, run, _, _ = variant_writer(PatchableWarehouse)
    expected_store = MemoryStore()
    roots = test_m_owned_writers.namespaces()
    for path, value in dump(store, roots).items():
        expected_store.set(path, value)
    expected = Database(expected_store, Registry(Product, Lot, PatchableWarehouse))
    assert run("ownerpatch", {"label": "receiving"}) == "ok"
    expected.patch(
        PatchableWarehouse, "MAIN", at=("locations", "DOCK"), changes={"label": "receiving"}
    )
    assert dump(store, roots) == dump(expected_store, roots)
    assert db.get(PatchableWarehouse, "MAIN", at=("locations", "STORAGE")).label == "zone"
    assert db.check_indexes(PatchableWarehouse).issues == ()
