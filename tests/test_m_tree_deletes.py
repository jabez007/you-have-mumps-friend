# SPDX-License-Identifier: GPL-3.0-or-later
"""Populated M deletes retain Python layouts and validate net ancestor effects."""

import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import Lot, Product, Warehouse
from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    Path,
    Reference,
    Registry,
)
from tests import test_m_owned_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_native_schema_safety import ready

m_writer = test_m_owned_writers.m_writer
variant_writer = test_m_owned_writers.variant_writer
owned_writer = test_m_owned_writers.owned_writer


def clone(store, model):
    other = MemoryStore()
    for path, raw in dump(store, test_m_owned_writers.namespaces()).items():
        other.set(path, raw)
    return Database(other, Registry(Product, Lot, model))


@pytest.mark.native
@pytest.mark.parametrize("model", [Warehouse, test_m_owned_writers.IndexedWarehouse])
@pytest.mark.parametrize(
    "scope,at", [("bin", ("locations", "DOCK", "bins", "B1")), ("location", ("locations", "DOCK"))]
)
def test_populated_subtree_delete_matches_python_indexes_totals_and_references(
    store, variant_writer, model, scope, at
):
    db, run, _, _ = variant_writer(
        model, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    expected = clone(store, model)
    key = "B1" if scope == "bin" else "DOCK"
    assert run("emptydelete", {"_scope": scope, "id": key}) == "ok"
    expected.delete_child(model, "MAIN", at=at)
    assert dump(store, test_m_owned_writers.namespaces()) == dump(
        expected.store, test_m_owned_writers.namespaces()
    )
    assert db.get(model, "MAIN").stored_on_hand == 3
    assert db.check_indexes(model).issues == ()


@pytest.mark.native
def test_whole_root_delete_matches_python_and_preserves_catalog_records(store, variant_writer):
    db, run, _, _ = variant_writer(
        Warehouse, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    expected = clone(store, Warehouse)
    assert run("delete", {"id": "MAIN"}) == "ok"
    expected.delete(Warehouse, "MAIN")
    assert dump(store, test_m_owned_writers.namespaces()) == dump(
        expected.store, test_m_owned_writers.namespaces()
    )
    assert db.get(Product, "WIDGET").id == "WIDGET"
    assert db.get(Lot, "LOT-001").id == "LOT-001"
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_failed_final_unique_total_restores_deleted_children_counts_and_edges(
    store, variant_writer
):
    model = test_m_owned_writers.UniqueTotalWarehouse
    values = test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    values["locations"]["EMPTY"] = {"id": "EMPTY", "bins": {}}
    db, run, _, _ = variant_writer(model, values)
    before = dump(store, test_m_owned_writers.namespaces())
    assert run("emptydelete", {"_scope": "bin", "id": "B1"}) == "unique"
    assert dump(store, test_m_owned_writers.namespaces()) == before
    assert db.check_indexes(model).issues == ()


class Item(Model):
    id: str = Field(primary_key=True)
    qty: Decimal


class Group(Model):
    id: str = Field(primary_key=True)
    items: Children[Item] = ChildField(key="id", min_length=1)


class GroupRoot(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    groups: Children[Group] = ChildField(key="id", min_length=1)
    total: Decimal = Field(sum_of="groups.items.qty", index=True, ge=0)


@pytest.mark.native
def test_subtree_uses_net_sum_and_relaxes_only_removed_owner_minimums(store, m_writer):
    db = Database(store, Registry(GroupRoot))
    expected = Database(MemoryStore(), Registry(GroupRoot))
    root = GroupRoot(
        id="MAIN",
        groups={
            "DROP": Group(
                id="DROP",
                items={"A": Item(id="A", qty=Decimal(20)), "B": Item(id="B", qty=Decimal(-20))},
            ),
            "KEEP": Group(id="KEEP", items={"K": Item(id="K", qty=Decimal(10))}),
        },
    )
    for target in (db, expected):
        target.insert(root)
    _, run, routine, _ = m_writer
    replace_source(routine, db.m_routine(GroupRoot, name="YHMFTAPI"))
    assert run("groupdelete", {"id": "DROP"}) == "ok"
    expected.delete_child(GroupRoot, "MAIN", at=("groups", "DROP"))
    roots = (GroupRoot.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef")
    assert dump(store, roots) == dump(expected.store, roots)
    before = dump(store, roots)
    assert run("groupdelete", {"id": "KEEP"}) == "collection_limit"
    assert dump(store, roots) == before
    assert db.get(GroupRoot, "MAIN").total == 10
    assert run("delete", {"id": "MAIN"}) == "ok"
    expected.delete(GroupRoot, "MAIN")
    assert dump(store, roots) == dump(expected.store, roots)


@pytest.mark.native
def test_incoming_ledger_reference_blocks_populated_root_delete_before_changes(
    store, variant_writer
):
    from examples.inventory import MODELS, Movement, Position

    _, run, _, _ = variant_writer(
        Warehouse, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    db = Database(store, Registry(*MODELS))
    db.insert(
        Movement(
            id="HISTORY",
            kind="receive",
            product=Reference[Product](key="WIDGET"),
            lot=Reference[Lot](key="LOT-001"),
            quantity=Decimal(2),
            destination=Position(
                warehouse=Reference[Warehouse](key="MAIN"), location="DOCK", bin="B1"
            ),
            reason="Existing ledger",
            occurred_at=datetime(2026, 11, 7, tzinfo=UTC),
        )
    )
    before = dump(store, test_m_owned_writers.namespaces())
    assert run("delete", {"id": "MAIN"}) == "references"
    assert dump(store, test_m_owned_writers.namespaces()) == before


@pytest.mark.native
def test_corrupt_concrete_child_key_cannot_be_hidden_by_subtree_delete(store, variant_writer):
    db, run, _, _ = variant_writer(
        Warehouse, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    old = db.raw_path(
        Warehouse, "MAIN", at=("locations", "DOCK", "bins", "B1", "balances", "LOT-001")
    )
    new = Path(old.global_name, old.subscripts[:-1] + (b"raw",))
    for path, value in dump(store, (Warehouse.__global__,)).items():
        if path.subscripts[: len(old.subscripts)] == old.subscripts:
            store.set(
                Path(path.global_name, new.subscripts + path.subscripts[len(old.subscripts) :]),
                value,
            )
    store.delete_tree(old)
    before = dump(store, test_m_owned_writers.namespaces())
    assert run("emptydelete", {"_scope": "bin", "id": "B1"}) == "corrupt"
    assert dump(store, test_m_owned_writers.namespaces()) == before


@pytest.mark.native
def test_delete_restarts_after_concurrent_quantity_change_and_recomputes_net_delta(
    store, variant_writer
):
    db, _, routine, environment = variant_writer(
        Warehouse, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    source = routine.read_text()
    marker = " ; YHMF subtree owner checkpoint:"
    pause = (
        ' if node="locations.bins.balances",$trestart=0 do\n'
        ' . new reply\n . write "ready",!\n . read reply\n'
    )
    replace_source(routine, source.replace(marker, pause + marker))
    worker = subprocess.Popen(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "subdeleteworker^YHMFWTEST"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        db.patch(
            Warehouse,
            "MAIN",
            at=("locations", "DOCK", "bins", "B1", "balances", "LOT-001"),
            changes={"quantity": Decimal(4)},
        )
        expected = clone(store, Warehouse)
        output, error = worker.communicate("go\n", timeout=30)
        assert worker.returncode == 0 and not error, error
        assert output.strip() == "ok"
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
    expected.delete_child(Warehouse, "MAIN", at=("locations", "DOCK", "bins", "B1"))
    assert dump(store, test_m_owned_writers.namespaces()) == dump(
        expected.store, test_m_owned_writers.namespaces()
    )
    assert db.get(Warehouse, "MAIN").stored_on_hand == 3
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_error_after_descendant_cleanup_restores_data_indexes_and_references(store, variant_writer):
    _, run, routine, _ = variant_writer(
        Warehouse, test_m_owned_writers.warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    before = dump(store, test_m_owned_writers.namespaces())
    source = routine.read_text()
    marker = " ; YHMF subtree checkpoint:"
    replace_source(routine, source.replace(marker, ' set $ecode=",U1001,"\n' + marker))
    assert run("subdeletecaught", {}) == "error|0"
    assert dump(store, test_m_owned_writers.namespaces()) == before
