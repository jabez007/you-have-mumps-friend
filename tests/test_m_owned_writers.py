# SPDX-License-Identifier: GPL-3.0-or-later
import subprocess
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from examples.inventory import (
    MODELS,
    Balance,
    Bin,
    BinAddress,
    Inventory,
    Location,
    Lot,
    Movement,
    Product,
    Supplier,
    Warehouse,
)
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.errors import ReferenceViolation, SchemaError
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_native_schema_safety import ready

m_writer = test_m_writers.m_writer


class IndexedBalance(Balance):
    quantity: Decimal = Field(default=Decimal(0), ge=0, max_digits=18, decimal_places=6, index=True)


class IndexedBin(Bin):
    balances: Children[IndexedBalance] = ChildField(key="id")


class IndexedLocation(Location):
    bins: Children[IndexedBin] = ChildField(key="id")


class IndexedWarehouse(Warehouse):
    __schema_version__ = 4
    locations: Children[IndexedLocation] = ChildField(key="id")


class UniqueBalance(Balance):
    quantity: Decimal = Field(
        default=Decimal(0), ge=0, max_digits=18, decimal_places=6, unique=True
    )


class UniqueBin(Bin):
    balances: Children[UniqueBalance] = ChildField(key="id")


class UniqueSourceLocation(Location):
    bins: Children[UniqueBin] = ChildField(key="id")


class UniqueSourceWarehouse(Warehouse):
    __schema_version__ = 4
    locations: Children[UniqueSourceLocation] = ChildField(key="id")


class UniqueTotalLocation(Location):
    stored_on_hand: Decimal = Field(sum_of="bins.balances.quantity", unique=True, ge=0)


class UniqueTotalWarehouse(Warehouse):
    __schema_version__ = 4
    locations: Children[UniqueTotalLocation] = ChildField(key="id")


class BoundedBin(Bin):
    balances: Children[Balance] = ChildField(key="id", min_length=1, max_length=1)


class BoundedLocation(Location):
    bins: Children[BoundedBin] = ChildField(key="id")


class BoundedWarehouse(Warehouse):
    __schema_version__ = 4
    locations: Children[BoundedLocation] = ChildField(key="id")


def warehouse_values(dock, storage):
    return {
        "id": "MAIN",
        "name": "Main warehouse",
        "locations": {
            name: {
                "id": name,
                "bins": {
                    "B1": {
                        "id": "B1",
                        "balances": {
                            key: {
                                "id": key,
                                "lot": {"key": "LOT-001"},
                                "product": {"key": "WIDGET"},
                                "quantity": Decimal(value),
                            }
                            for key, value in quantities.items()
                        },
                    }
                },
            }
            for name, quantities in (("DOCK", dock), ("STORAGE", storage))
        },
    }


def namespaces():
    return tuple(model.__global__ for model in MODELS) + (
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
        "^YHMFMigration",
    )


@pytest.mark.parametrize("model", [UniqueSourceWarehouse, UniqueTotalWarehouse, BoundedWarehouse])
def test_owned_export_accepts_qualified_unique_and_collection_constraints(model):
    db = Database(MemoryStore(), Registry(Product, Lot, model))
    model.model_validate(warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"}))
    assert "insertchild(rootkey,path,input)" in db.m_routine(model, name="WarehouseAPI")


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


@pytest.fixture
def variant_writer(store, m_writer):
    _, run, routine, environment = m_writer

    def create(model, values=None):
        for name in namespaces():
            store.delete_tree(Path(name))
        baseline = Database(MemoryStore(), Registry(*MODELS))
        seed(baseline)
        db = Database(store, Registry(Product, Lot, model))
        db.insert(baseline.get(Product, "WIDGET"))
        db.insert(baseline.get(Lot, "LOT-001"))
        root = baseline.get(Warehouse, "MAIN").model_dump(exclude={"total_on_hand"})
        db.insert(model.model_validate(root if values is None else values))
        replace_source(routine, db.m_routine(model, name="YHMFTAPI"))
        return db, run, routine, environment

    return create


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


@pytest.mark.native
def test_native_m_nodes_match_the_same_public_python_child_operations(store, owned_writer):
    db, run, _, _ = owned_writer
    expected_store = MemoryStore()
    expected = Database(expected_store, Registry(*MODELS))
    seed(expected)
    path = ("locations", "DOCK", "bins", "B1", "balances")
    assert (
        run(
            "ownedinsert",
            {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2.50"},
        )
        == "ok"
    )
    expected.insert_child(
        Warehouse,
        "MAIN",
        collection=path,
        record=Balance(
            id="LOT-001",
            lot=Reference[Lot](key="LOT-001"),
            product=Reference[Product](key="WIDGET"),
            quantity=Decimal("2.50"),
        ),
    )
    assert dump(store, namespaces()) == dump(expected_store, namespaces())
    assert run("ownedpatch", {"quantity": "3.75"}) == "ok"
    expected.patch(Warehouse, "MAIN", at=(*path, "LOT-001"), changes={"quantity": Decimal("3.75")})
    assert dump(store, namespaces()) == dump(expected_store, namespaces())
    assert run("owneddelete", {}) == "ok"
    expected.delete_child(Warehouse, "MAIN", at=(*path, "LOT-001"))
    assert dump(store, namespaces()) == dump(expected_store, namespaces())


@pytest.mark.native
@pytest.mark.parametrize("model", [UniqueSourceWarehouse, UniqueTotalWarehouse])
def test_owned_unique_failure_restores_source_totals_and_all_earlier_index_changes(
    store, variant_writer, model
):
    db, run, _, _ = variant_writer(model, warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"}))
    before = dump(store, namespaces())
    assert run("ownedpatch", {"quantity": "3"}) == "unique"
    assert dump(store, namespaces()) == before
    assert db.check_indexes(model).issues == ()
    assert run("ownedpatch", {"quantity": "4"}) == "ok"
    assert db.get(model, "MAIN").stored_on_hand == 7
    assert db.check_indexes(model).issues == ()


@pytest.mark.native
def test_owned_collection_limits_preserve_all_values_on_insertion_and_deletion(
    store, variant_writer
):
    db, run, _, _ = variant_writer(
        BoundedWarehouse, warehouse_values({"LOT-001": "2"}, {"LOT-001": "3"})
    )
    before = dump(store, namespaces())
    assert (
        run("ownedinsert", {"id": "extra", "lot": "LOT-001", "product": "WIDGET", "quantity": "1"})
        == "collection_limit"
    )
    assert run("owneddelete", {}) == "collection_limit"
    assert dump(store, namespaces()) == before
    assert db.check_indexes(BoundedWarehouse).issues == ()


@pytest.mark.native
def test_owned_insertion_honors_the_same_root_node_budget_as_python(store, variant_writer):
    db, run, _, _ = variant_writer(Warehouse)
    # A single aggregate insert exhausts the default native TP buffers first.
    # Reach the mapper node boundary through bounded public transactions.
    for location, size in (("DOCK", 1000), ("STORAGE", 995)):
        for offset in range(0, size, 50):
            records = tuple(
                Balance(
                    id=f"B{i}",
                    lot=Reference[Lot](key="LOT-001"),
                    product=Reference[Product](key="WIDGET"),
                    quantity=Decimal(1),
                )
                for i in range(offset, min(offset + 50, size))
            )

            def insert_batch(tx, records=records, location=location):
                for record in records:
                    tx.insert_child(
                        Warehouse,
                        "MAIN",
                        collection=("locations", location, "bins", "B1", "balances"),
                        record=record,
                    )

            db.atomic(insert_batch)
    before = dump(store, namespaces())
    assert (
        run(
            "ownedinsert",
            {
                "_location": "STORAGE",
                "id": "extra",
                "lot": "LOT-001",
                "product": "WIDGET",
                "quantity": "1",
            },
        )
        == "node_budget"
    )
    assert dump(store, namespaces()) == before
    assert db.get(Warehouse, "MAIN").stored_on_hand == 1995
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_owned_reference_replacement_uses_exact_dotted_indexes_and_reverse_locations(
    store, owned_writer
):
    db, run, _, _ = owned_writer
    db.insert(Product(id="GADGET", sku="GADGET", name="Gadget"))
    db.insert(Lot(id="LOT-002", product=Reference[Product](key="GADGET")))
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "ok"
    )
    assert run("ownedpatch", {"product": "GADGET", "lot": "LOT-002"}) == "ok"
    assert db.referrers(Reference[Lot](key="LOT-001"), source=Warehouse) == []
    assert db.referrers(Reference[Product](key="WIDGET"), source=Warehouse) == []
    for reference in (Reference[Product](key="GADGET"), Reference[Lot](key="LOT-002")):
        (hit,) = db.referrers(reference, source=Warehouse)
        assert hit.at == ("locations", "DOCK", "bins", "B1", "balances", "LOT-001")
    root = db.get(Warehouse, "MAIN")
    assert root.stored_on_hand == root.locations["DOCK"].stored_on_hand == 2
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("target", [Product, Warehouse])
def test_owned_maintenance_blocks_only_affected_reads_and_allows_reference_release(
    store, owned_writer, target
):
    db, run, _, _ = owned_writer
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "ok"
    )
    job = db.begin_index_rebuild(target, plan=db.plan_index_rebuild(target))
    if target is Warehouse:
        before = dump(store, namespaces())
        assert run("ownedpatch", {"quantity": "3"}) == "maintenance"
        assert dump(store, namespaces()) == before
    else:
        assert run("ownedpatch", {"quantity": "3"}) == "ok"
        before = dump(store, namespaces())
        assert run("ownedpatch", {"product": "WIDGET"}) == "maintenance"
        assert dump(store, namespaces()) == before
        assert run("owneddelete", {}) == "ok"
    db.abort_migration(job.migration_id)
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("raw", [False, True])
def test_owned_operations_never_install_or_adopt_a_root_schema(store, owned_writer, raw):
    _, run, _, _ = owned_writer
    for name in namespaces():
        store.delete_tree(Path(name))
    if raw:
        store.set(Path("^InventoryWarehouse", (b"s:OTHER", b"raw")), b"unmanaged")
    before = dump(store, namespaces())
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "schema"
    )
    assert dump(store, namespaces()) == before


@pytest.mark.native
@pytest.mark.parametrize("fail", [False, True])
def test_owned_participants_commit_together_or_abort_the_callers_prior_changes(
    store, owned_writer, fail
):
    db, run, _, _ = owned_writer
    for location in ("DOCK", "STORAGE"):
        assert (
            run(
                "ownedinsert",
                {
                    "_location": location,
                    "id": "LOT-001",
                    "lot": "LOT-001",
                    "product": "WIDGET",
                    "quantity": "2",
                },
            )
            == "ok"
        )
    before = dump(store, namespaces())
    assert run("ownedpair", {"quantity": "3.5", "_fail": "1" if fail else "0"}) == (
        "invalid|0" if fail else "ok|0"
    )
    if fail:
        assert dump(store, namespaces()) == before
    else:
        root = db.get(Warehouse, "MAIN")
        assert root.stored_on_hand == Decimal("7.75")
        assert root.locations["DOCK"].stored_on_hand == Decimal("3.5")
        assert root.locations["STORAGE"].stored_on_hand == Decimal("4.25")
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_owned_transaction_boundaries_preserve_callers_and_reject_missing_transactions(
    store, owned_writer
):
    _, run, _, _ = owned_writer
    before = dump(store, namespaces())
    assert run("ownedoutside", {"quantity": "3"}) == "transaction"
    assert run("ownednested", {"quantity": "3"}) == "nested|2"
    assert dump(store, namespaces()) == before


@pytest.mark.native
def test_second_owned_participant_restarts_the_entire_caller_without_double_applying_totals(
    store, owned_writer
):
    db, run, routine, _ = owned_writer
    for location in ("DOCK", "STORAGE"):
        assert (
            run(
                "ownedinsert",
                {
                    "_location": location,
                    "id": "LOT-001",
                    "lot": "LOT-001",
                    "product": "WIDGET",
                    "quantity": "2",
                },
            )
            == "ok"
        )
    marker = " ; YHMF owned mutation complete:"
    source = routine.read_text()
    assert source.count(marker) == 1
    replace_source(
        routine, source.replace(marker, ' if $trestart=0,parts(2)="s:STORAGE" trestart\n' + marker)
    )
    assert run("ownedpair", {"quantity": "3.5"}) == "ok|0"
    root = db.get(Warehouse, "MAIN")
    assert root.stored_on_hand == Decimal("7.75")
    assert root.locations["DOCK"].stored_on_hand == Decimal("3.5")
    assert root.locations["STORAGE"].stored_on_hand == Decimal("4.25")
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_native_owned_sums_exceed_m_numeric_precision_without_rounding(store, owned_writer):
    db, run, _, _ = owned_writer
    quantity = "999999999999.999999"
    for location in ("DOCK", "STORAGE"):
        assert (
            run(
                "ownedinsert",
                {
                    "_location": location,
                    "id": "LOT-001",
                    "lot": "LOT-001",
                    "product": "WIDGET",
                    "quantity": quantity,
                },
            )
            == "ok"
        )
    assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("1999999999999.999998")
    assert run("ownedpatch", {"quantity": "0.000001"}) == "ok"
    assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("1000000000000")
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_owned_deletion_preserves_the_roots_existing_incoming_ledger_references(
    store, owned_writer
):
    db, run, _, _ = owned_writer
    Inventory(db).receive("R", "LOT-001", BinAddress("MAIN", "DOCK", "B1"), Decimal(2))
    before = dump(store, ("^InventoryMovement",))
    assert run("owneddelete", {}) == "ok"
    assert dump(store, ("^InventoryMovement",)) == before
    assert len(db.referrers(Reference[Warehouse](key="MAIN"), source=Movement)) == 1
    with pytest.raises(ReferenceViolation):
        db.delete(Warehouse, "MAIN")
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("damage", ["metadata", "parent", "count", "sum"])
def test_corrupt_selected_hierarchy_state_cannot_commit_partial_m_writes(
    store, owned_writer, damage
):
    _, run, _, _ = owned_writer
    assert (
        run(
            "ownedinsert", {"id": "LOT-001", "lot": "LOT-001", "product": "WIDGET", "quantity": "2"}
        )
        == "ok"
    )
    root = Path("^InventoryWarehouse", (b"s:MAIN",))
    if damage == "metadata":
        store.set(root.child(b"%m", b"fingerprint"), b"bad")
    elif damage == "parent":
        store.set(root.child(b"locations", b"s:DOCK"), b"o:0")
    elif damage == "count":
        store.set(root.child(b"locations", b"s:DOCK", b"bins", b"s:B1", b"balances"), b"c:01")
    else:
        store.set(root.child(b"stored_on_hand"), b"i:2")
    before = dump(store, namespaces())
    assert run("ownedpatch", {"quantity": "3"}) == "corrupt"
    assert dump(store, namespaces()) == before


def _owned_worker(routine, environment):
    original = routine.read_text()
    marker = " ; YHMF writer checkpoint:"
    assert original.count(marker) == 1
    replace_source(
        routine,
        original.replace(
            marker,
            ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
            ' . if reply\'="go" set $ecode=",U1004,"\n' + marker,
        ),
    )
    command = (
        'set at(1)="locations",at(2)="DOCK",at(3)="bins",at(4)="B1",'
        'at(5)="balances",at(6)="LOT-001",input("quantity")="3" '
        'write $$patchchild^YHMFTAPI("MAIN",.at,.input),!'
    )
    worker = subprocess.Popen(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "%XCMD", command],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return worker, original


@pytest.mark.native
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize(
    "mode", ["different_children", "same_child", "parent_delete", "root_delete"]
)
def test_native_m_child_races_restart_against_current_python_ownership_and_totals(
    store, owned_writer, variant_writer, indexed, mode
):
    model = IndexedWarehouse if indexed else Warehouse
    db, run, routine, environment = variant_writer(model) if indexed else owned_writer
    for location in ("DOCK", "STORAGE"):
        assert (
            run(
                "ownedinsert",
                {
                    "_location": location,
                    "id": "LOT-001",
                    "lot": "LOT-001",
                    "product": "WIDGET",
                    "quantity": "2",
                },
            )
            == "ok"
        )
    worker, original = _owned_worker(routine, environment)
    try:
        ready(worker)
        if mode == "root_delete":
            assert db.delete(model, "MAIN")
        elif mode == "parent_delete":
            db.delete_child(model, "MAIN", at=("locations", "DOCK"))
        else:
            location = "STORAGE" if mode == "different_children" else "DOCK"
            db.patch(
                model,
                "MAIN",
                at=("locations", location, "bins", "B1", "balances", "LOT-001"),
                changes={"quantity": Decimal(4)},
            )
        output, error = worker.communicate("go\n", timeout=20)
        assert worker.returncode == 0 and not error, error
        assert output.strip() == ("missing" if "delete" in mode else "ok")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
        replace_source(routine, original)
    if mode == "root_delete":
        assert db.scan(model) == []
    else:
        root = db.get(model, "MAIN")
        assert root.stored_on_hand == (
            2 if mode == "parent_delete" else 7 if mode == "different_children" else 5
        )
        if mode == "parent_delete":
            assert "DOCK" not in root.locations
    assert db.check_indexes(model).issues == ()
