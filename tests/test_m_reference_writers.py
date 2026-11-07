# SPDX-License-Identifier: GPL-3.0-or-later
"""M and Python share managed reference identities and transactional counts."""

import subprocess
from pathlib import Path as FilePath

import pytest

from examples.inventory import Lot, Product
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation, SchemaError
from mccann_hub.yhmf.references import reference_root
from mccann_hub.yhmf.schemas import schema_path
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_native_schema_safety import ready
from tests.test_schema_contracts import seed_legacy

m_writer = test_m_writers.m_writer


@pytest.fixture
def reference_writer(store, m_writer):
    _, run, routine, environment = m_writer

    def bind(model=Lot, targets=(Product,)):
        db = Database(store, Registry(*dict.fromkeys((*targets, model))))
        if Product in targets:
            db.insert(Product(id="P", sku="SKU-P", name="Product P"))
            db.insert(Product(id="Q", sku="SKU-Q", name="Product Q"))
        replace_source(routine, db.m_routine(model, name="YHMFTAPI"))
        return db, run, routine, environment

    return bind


def namespaces(model=Lot):
    return (
        model.__global__,
        Product.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    )


def test_reference_export_is_read_only_and_binds_registered_targets():
    store = MemoryStore()
    before = dump(store, namespaces())
    db = Database(store, Registry(Product, Lot))
    source = db.m_routine(Lot, name="LotAPI")
    assert 'cfg("ref","product")="InventoryProduct"' in source
    assert dump(store, namespaces()) == before


@pytest.mark.parametrize("change", ["normalization", "annotation", "constraint", "alias"])
def test_nonstandard_reference_key_validation_is_rejected_at_export(change):
    class Target(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)

    link = Reference[Target]
    if change == "normalization":
        link.model_config = dict(link.model_config, str_to_lower=True)
    elif change == "annotation":
        link.model_fields["key"].annotation = str
    else:
        from pydantic import Field as PydanticField

        link.model_fields["key"] = (
            PydanticField(alias="external") if change == "alias" else PydanticField(min_length=2)
        )
        link.model_fields["key"].annotation = str | int
    link.model_rebuild(force=True)

    class Source(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        target: link

    with pytest.raises(SchemaError, match="standard Reference"):
        Database(MemoryStore(), Registry(Target, Source)).m_routine(Source, name="SourceAPI")


@pytest.mark.native
def test_plain_m_lot_crud_preserves_referrers_counts_and_python_restrictions(
    store, reference_writer
):
    db, run, _, _ = reference_writer()
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    assert db.get(Lot, "L").product == Reference[Product](key="P")
    assert len(db.referrers(Reference[Product](key="P"), source=Lot)) == 1
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "P")
    assert run("replace", {"id": "L", "product": "Q"}) == "ok"
    assert db.referrers(Reference[Product](key="P"), source=Lot) == []
    assert db.delete(Product, "P")
    with pytest.raises(ReferenceViolation):
        db.delete(Product, "Q")
    assert run("delete", {"id": "L"}) == "ok"
    assert db.referrers(Reference[Product](key="Q"), source=Lot) == []
    assert db.delete(Product, "Q")
    assert db.check_indexes(Lot).issues == ()
    assert store.children(Path("^YHMFRef"), limit=1) == ()


@pytest.mark.native
def test_m_reference_record_index_reverse_entry_and_count_bytes_match_python(
    store, reference_writer
):
    _, run, _, _ = reference_writer()
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    expected = MemoryStore()
    db = Database(expected, Registry(Product, Lot))
    db.insert(Product(id="P", sku="SKU-P", name="Product P"))
    db.insert(Product(id="Q", sku="SKU-Q", name="Product Q"))
    db.insert(Lot(id="L", product=Reference[Product](key="P")))
    assert dump(store, namespaces()) == dump(expected, namespaces())


@pytest.mark.native
@pytest.mark.parametrize("damage", ["missing", "snapshot", "record", "identity", "count"])
def test_missing_or_untrusted_targets_leave_no_source_metadata_or_partial_links(
    store, reference_writer, damage
):
    db, run, _, _ = reference_writer()
    if damage == "missing":
        assert db.delete(Product, "P")
    elif damage == "snapshot":
        store.set(schema_path(Product.__global__).child(b"snapshot"), b"{}")
    elif damage == "record":
        store.set(Path(Product.__global__, (b"s:P", b"%m", b"type")), b"wrong")
    elif damage == "identity":
        store.set(Path(Product.__global__, (b"s:P", b"id")), b"s:wrong")
    else:
        store.set(reference_root(db.registry.spec(Product).path("P")), b"-1")
    before = dump(store, namespaces())
    assert run("insert", {"id": "L", "product": "P"}) in {"references", "schema", "corrupt"}
    assert dump(store, namespaces()) == before
    assert store.get(schema_path(Lot.__global__)) is None


@pytest.mark.native
def test_multiple_edges_share_exact_counts_and_swaps_preserve_unrelated_edges(
    store, reference_writer
):
    class Pair(Model):
        __global__ = "^InventoryLot"
        id: str = Field(primary_key=True)
        first: Reference[Product]
        second: Reference[Product]

    db, run, _, _ = reference_writer(Pair)
    assert run("insert", {"id": "L", "first": "P", "second": "P"}) == "ok"
    path = reference_root(db.registry.spec(Product).path("P"))
    assert store.get(path) == b"2"
    assert len(db.referrers(Reference[Product](key="P"), source=Pair)) == 2
    assert run("replace", {"id": "L", "first": "P", "second": "Q"}) == "ok"
    assert store.get(path) == b"1"
    assert run("replace", {"id": "L", "first": "Q", "second": "P"}) == "ok"
    assert store.get(path) == b"1"
    before = dump(store, namespaces(Pair))
    assert run("replace", {"id": "L", "first": "Q", "second": "MISSING"}) == "references"
    assert dump(store, namespaces(Pair)) == before
    assert run("delete", {"id": "L"}) == "ok"
    assert store.get(path) == b"0"
    assert db.delete(Product, "P")
    assert db.delete(Product, "Q")


@pytest.mark.native
def test_failed_unique_reference_change_rolls_back_old_claims_and_reverse_entries(
    store, reference_writer
):
    class UniqueLot(Lot):
        product: Reference[Product] = Field(unique=True)

    db, run, _, _ = reference_writer(UniqueLot)
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    db.insert(UniqueLot(id="OTHER", product=Reference[Product](key="Q")))
    before = dump(store, namespaces(UniqueLot))
    assert run("replace", {"id": "L", "product": "Q"}) == "unique"
    assert dump(store, namespaces(UniqueLot)) == before
    assert db.check_indexes(UniqueLot).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("mode", ["restart", "error"])
def test_reference_restart_and_late_rollback_cover_edges_and_counts(store, reference_writer, mode):
    db, run, routine, _ = reference_writer()
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    injection = " if $trestart=0 trestart\n" if mode == "restart" else ' set $ecode=",U1005,"\n'
    # Inject after edge/count writes, before returning to commit.
    marker = " ; YHMF reference changes complete:"
    source = routine.read_text()
    assert source.count(marker) == 1
    replace_source(routine, source.replace(marker, injection + marker))
    before = dump(store, namespaces())
    if mode == "restart":
        assert run("replace", {"id": "L", "product": "Q"}) == "ok"
        assert db.get(Lot, "L").product.key == "Q"
    else:
        assert run("caught", {"id": "OTHER", "product": "Q"}) == "error|0"
        assert dump(store, namespaces()) == before
    assert db.check_indexes(Lot).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "value,delta", [("0", -1), ("0", 16), ("1" + "0" * 100, -1), ("9" * 100, 1), ("1000", -16)]
)
def test_native_counter_arithmetic_is_exact_beyond_m_numeric_precision(
    reference_writer, value, delta
):
    _, run, _, _ = reference_writer()
    expected = "invalid" if int(value) + delta < 0 else str(int(value) + delta)
    assert run("counter", {"value": value, "delta": str(delta)}) == expected


@pytest.mark.native
def test_m_references_keep_legacy_target_binding_without_conversion(store, reference_writer):
    db, run, routine, _ = reference_writer()
    # Replace a fresh target installation with the supported legacy identity.
    store.delete_tree(Path(Product.__global__))
    store.delete_tree(Path("^YHMFSchema", (b"InventoryProduct",)))
    store.delete_tree(Path("^YHMFIndex", (b"InventoryProduct",)))
    store.delete_tree(Path("^YHMFUnique", (b"InventoryProduct",)))
    legacy = seed_legacy(store, Product(id="P", sku="SKU-P", name="Product P"))
    replace_source(routine, db.m_routine(Lot, name="YHMFTAPI"))
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    assert store.get(schema_path(Product.__global__)) == legacy.fingerprint
    assert db.get(Lot, "L").product.key == "P"
    assert db.check_indexes(Lot).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("key", ["bad\0key", "雪", "X" * 1000])
def test_unrepresentable_reference_keys_fail_before_source_installation(
    store, reference_writer, key
):
    _, run, _, _ = reference_writer()
    before = dump(store, namespaces())
    assert run("insert", {"id": "L", "product": key}) in {"invalid", "key_size"}
    assert dump(store, namespaces()) == before


@pytest.mark.native
def test_integer_target_keys_keep_the_declared_key_codec(store, reference_writer):
    class Numeric(Model):
        __global__ = "^YHMFTestOrder"
        id: int = Field(primary_key=True)

    class Source(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        target: Reference[Numeric] = Field(index=True)

    db, run, _, _ = reference_writer(Source, (Numeric,))
    db.insert(Numeric(id=2))
    assert run("insert", {"id": "S", "target": "2"}) == "ok"
    assert db.get(Source, "S").target == Reference[Numeric](key=2)
    assert store.get(Path(Source.__global__, (b"s:S", b"target"))) == b"r:2"
    assert run("replace", {"id": "S", "target": "02"}) == "invalid"
    assert db.check_indexes(Source).issues == ()


@pytest.mark.native
def test_nonnullable_self_reference_can_see_the_staged_managed_record(store, reference_writer):
    class Node(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        parent: "Reference[Node]"

    Node.model_rebuild(_types_namespace={"Node": Node})
    db, run, _, _ = reference_writer(Node, (Node,))
    assert run("insert", {"id": "N", "parent": "N"}) == "ok"
    assert db.get(Node, "N").parent == Reference[Node](key="N")
    assert store.get(reference_root(db.registry.spec(Node).path("N"))) == b"1"
    # The M delete contract remains conservatively restrict-only.
    assert run("delete", {"id": "N"}) == "references"


@pytest.mark.native
@pytest.mark.parametrize("winner", ["python", "m"])
def test_m_reference_insert_racing_python_target_delete_never_dangles(
    store, reference_writer, winner
):
    db, _, routine, environment = reference_writer()
    replace_source(
        routine,
        routine.read_text().replace(
            " ; YHMF reference checkpoint:",
            ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
            ' . if reply\'="go" set $ecode=",U1004,"\n ; YHMF reference checkpoint:',
        ),
    )
    command = str(FilePath(environment["ydb_dist"]) / "yottadb")
    worker = subprocess.Popen(
        [command, "-run", "%XCMD", 'do refworker^YHMFWTEST("L","P")'],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready(worker)
        if winner == "python":
            assert db.delete(Product, "P")
        output, error = worker.communicate("go\n", timeout=20)
        assert worker.returncode == 0 and not error, error
        assert output.strip() == ("references" if winner == "python" else "ok")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=5)
    if winner == "python":
        assert db.scan(Lot) == []
        assert store.get(schema_path(Lot.__global__)) is None
    else:
        with pytest.raises(ReferenceViolation):
            db.delete(Product, "P")
        assert db.get(Lot, "L").product.key == "P"
        assert len(db.referrers(Reference[Product](key="P"), source=Lot)) == 1
    assert db.check_indexes(Lot).issues == ()


@pytest.mark.native
def test_target_maintenance_blocks_new_links_but_allows_source_deletion(store, reference_writer):
    db, run, _, _ = reference_writer()
    assert run("insert", {"id": "L", "product": "P"}) == "ok"

    class NextProduct(Product):
        __schema_version__ = 2
        note: str = "new"

    manager = Database(store, Registry(NextProduct))
    job = manager.begin_migration(Product, NextProduct, plan=manager.plan_migration(NextProduct))
    before = dump(store, namespaces())
    assert run("insert", {"id": "NEW", "product": "P"}) == "maintenance"
    assert dump(store, namespaces()) == before
    assert run("delete", {"id": "L"}) == "ok"
    manager.abort_migration(job.migration_id)
    assert run("insert", {"id": "AFTER", "product": "P"}) == "ok"
    assert db.check_indexes(Lot).issues == ()


@pytest.mark.native
def test_changed_target_schema_requires_regeneration_without_source_migration(
    store, reference_writer
):
    db, run, routine, _ = reference_writer()
    assert run("insert", {"id": "L", "product": "P"}) == "ok"
    original_source = db.registry.spec(Lot).snapshot

    class NextProduct(Product):
        __schema_version__ = 2
        note: str = "new"

    class CurrentLot(Lot):
        product: Reference[NextProduct] = Field(index=True)

    manager = Database(store, Registry(NextProduct))
    job = manager.begin_migration(Product, NextProduct, plan=manager.plan_migration(NextProduct))
    for _ in range(30):
        job = manager.advance_migration(job.migration_id, Product, NextProduct)
        if job.phase == "complete":
            break
    assert job.phase == "complete"
    assert run("insert", {"id": "STALE", "product": "P"}) == "schema"
    current = Database(store, Registry(NextProduct, CurrentLot))
    assert current.registry.spec(CurrentLot).snapshot == original_source
    replace_source(routine, current.m_routine(CurrentLot, name="YHMFTAPI"))
    assert run("insert", {"id": "CURRENT", "product": "P"}) == "ok"
    assert current.get(CurrentLot, "CURRENT").product.key == "P"
    assert current.check_indexes(CurrentLot).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("mode", ["success", "failure", "restart"])
def test_reference_participants_see_staged_targets_and_rollback_complete_groups(
    store, reference_writer, mode
):
    db, run, routine, _ = reference_writer()
    routine.with_name("YHMFPAPI.m").write_text(db.m_routine(Product, name="YHMFPAPI"))
    if mode == "restart":
        marker = " ; YHMF reference changes complete:"
        source = routine.read_text()
        assert source.count(marker) == 1
        replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    before = dump(store, namespaces())
    output = run("refpair", {"id": "L", "product": "NEW", "fail": str(int(mode == "failure"))})
    if mode == "failure":
        assert output == "unique|0"
        assert dump(store, namespaces()) == before
    else:
        assert output == "ok|0"
        assert db.get(Product, "NEW").sku == "NEW-SKU"
        assert db.get(Lot, "L").product.key == "NEW"
        assert len(db.referrers(Reference[Product](key="NEW"), source=Lot)) == 1
        assert db.check_indexes(Product).issues == db.check_indexes(Lot).issues == ()
