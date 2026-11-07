# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest

from examples.inventory import MODELS, Lot, Product, Supplier, Warehouse
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import Database, Reference, Registry
from mccann_hub.yhmf.errors import SchemaError
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_writers.m_writer


def namespaces():
    return tuple(model.__global__ for model in MODELS) + (
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
        "^YHMFMigration",
    )


def test_warehouse_export_is_read_only_and_custom_owned_validators_stay_rejected(read_only_store):
    db = Database(read_only_store, Registry(*MODELS))
    source = db.m_routine(Warehouse, name="WarehouseAPI")
    assert "insertchild(rootkey,path,input)" in source
    assert "patchchildtx(rootkey,path,input)" in source
    assert '"sumtargets"' in source and '"locations.bins.balances"' in source
    assert max(map(len, source.splitlines())) < 1000
    assert source == db.m_routine(Warehouse, name="WarehouseAPI")
    assert not db.inspect_schema(Warehouse).installed
    with pytest.raises(SchemaError, match="custom validation"):
        db.m_routine(Supplier, name="SupplierAPI")


@pytest.fixture
def owned_writer(store, m_writer):
    _, run, routine, environment = m_writer
    db = Database(store, Registry(*MODELS))
    seed(db)
    replace_source(routine, db.m_routine(Warehouse, name="YHMFTAPI"))
    return db, run, routine, environment


@pytest.mark.native
def test_plain_m_child_crud_maintains_owned_totals_and_exact_reverse_locations(store, owned_writer):
    db, run, _, _ = owned_writer
    assert (
        run(
            "ownedinsert",
            {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2.50"},
        )
        == "ok"
    )
    root = db.get(Warehouse, "MAIN")
    assert root.stored_on_hand == root.locations["DOCK"].stored_on_hand == Decimal("2.5")
    assert (
        db.count(Warehouse, "MAIN", collection=("locations", "DOCK", "bins", "B1", "balances")) == 1
    )
    hits = db.referrers(Reference[Product](key="WIDGET"), source=Warehouse)
    assert len(hits) == 1 and hits[0].root_key == "MAIN"
    assert hits[0].at == ("locations", "DOCK", "bins", "B1", "balances", "LOT-001")
    assert db.check_indexes(Warehouse).issues == ()
    before_refs = dump(store, ("^YHMFRef",))
    assert run("ownedpatch", {"quantity": "3.75"}) == "ok"
    assert dump(store, ("^YHMFRef",)) == before_refs
    assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("3.75")
    assert db.get(Warehouse, "MAIN", at=("locations", "DOCK")).stored_on_hand == Decimal("3.75")
    assert db.check_indexes(Warehouse).issues == ()
    assert run("owneddelete", {}) == "ok"
    assert db.get(Warehouse, "MAIN").stored_on_hand == 0
    assert db.get(Warehouse, "MAIN", at=("locations", "DOCK")).stored_on_hand == 0
    assert db.referrers(Reference[Lot](key="LOT-001"), source=Warehouse) == []
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"quantity": "-1"}, "invalid"),
        ({"quantity": "1E+18"}, "invalid"),
        ({"quantity": "1E-19"}, "invalid"),
        ({"id": "other"}, "invalid"),
        ({"unknown": "x"}, "invalid"),
        ({"product": "missing"}, "references"),
        ({"_location": "missing", "quantity": "1"}, "missing"),
    ],
)
def test_failed_m_child_changes_leave_all_data_and_metadata_unchanged(
    store, owned_writer, changes, status
):
    db, run, _, _ = owned_writer
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "ok"
    )
    before = dump(store, namespaces())
    assert run("ownedpatch", changes) == status
    assert dump(store, namespaces()) == before
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_existing_whole_m_labels_refuse_hierarchical_replacement_without_changes(
    store, owned_writer
):
    _, run, _, _ = owned_writer
    before = dump(store, namespaces())
    assert run("insert", {"id": "NEW"}) == "operation"
    assert run("replace", {"id": "MAIN"}) == "operation"
    assert run("delete", {"id": "MAIN"}) == "operation"
    assert dump(store, namespaces()) == before


@pytest.mark.native
@pytest.mark.parametrize("mode", ["restart", "error"])
def test_owned_m_source_totals_and_indexes_restart_or_rollback_together(store, owned_writer, mode):
    db, run, routine, _ = owned_writer
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "ok"
    )
    before = dump(store, namespaces())
    marker = " ; YHMF owned mutation complete:"
    source = routine.read_text()
    assert source.count(marker) == 1
    injected = " if $trestart=0 trestart\n" if mode == "restart" else ' set $ecode=",U1005,"\n'
    replace_source(routine, source.replace(marker, injected + marker))
    if mode == "restart":
        assert run("ownedpatch", {"quantity": "3.5"}) == "ok"
        assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("3.5")
    else:
        assert run("ownedcaught", {"quantity": "3.5"}) == "error|0"
        assert dump(store, namespaces()) == before
    assert db.check_indexes(Warehouse).issues == ()
