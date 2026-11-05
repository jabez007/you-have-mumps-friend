# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Path, Reference, Registry
from mccann_hub.yhmf.errors import SchemaMismatch, TransactionError


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str
    sku: str = Field(unique=True)


class ProductV2(Product):
    __schema_version__ = 2
    note: str | None = Field(default=None, index=True)


def seed(store):
    db = Database(store, Registry(Product))
    db.insert(Product(id="P1", name="Main", sku="SKU1"))
    return db


def dump_globals(store):
    result = {}

    def visit(path):
        value = store.get(path)
        if value is not None:
            result[path] = value
        for subscript in store.children(path, limit=100):
            visit(path.child(subscript))

    for name in (
        "^YHMFTestProduct",
        "^YHMFTestOrder",
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    ):
        visit(Path(name))
    return result


def test_added_field_and_index_produce_an_ordered_read_only_plan(store, read_only_store):
    old_db = seed(store)
    before = dump_globals(store)
    db = Database(read_only_store, Registry(ProductV2))
    plan = db.plan_migration(ProductV2)
    assert [step.kind for step in plan.steps] == [
        "validate_source_schema",
        "backfill_field",
        "validate_records",
        "rebuild_index",
        "validate_indexes",
        "update_record_metadata",
        "activate_schema",
    ]
    backfill = plan.steps[1]
    assert backfill.path == ("note",) and backfill.default_json == b"null"
    assert not plan.requires_manual_review and not plan.destructive and not plan.is_noop
    assert plan.inspection.installed.schema_version == 1
    assert plan.inspection.declared.schema_version == 2
    assert dump_globals(store) == before
    assert old_db.get(Product, "P1").name == "Main"
    with pytest.raises(SchemaMismatch):
        db.get(ProductV2, "P1")


def test_plan_serialization_is_deterministic_and_is_not_an_executor(store, read_only_store):
    seed(store)
    db = Database(read_only_store, Registry(ProductV2))
    first = db.plan_migration(ProductV2).to_json()
    assert first == db.plan_migration(ProductV2).to_json()
    document = json.loads(first)
    assert document["format_version"] == 1 and document["execution_supported"] is False
    assert document["source"]["fingerprint"] != document["target"]["fingerprint"]
    assert "default" not in document["steps"][1]
    assert document["steps"][1]["default_source"] == "declared"
    assert (
        db.plan_migration(ProductV2).to_dict(include_defaults=True)["steps"][1]["default"] is None
    )


def test_matching_schemas_have_a_noop_plan(store, read_only_store):
    seed(store)
    plan = Database(read_only_store, Registry(Product)).plan_migration(Product)
    assert plan.is_noop and plan.changes == () and plan.steps == ()


def test_an_empty_global_gets_an_install_plan_without_installation(store, read_only_store):
    plan = Database(read_only_store, Registry(Product)).plan_migration(Product)
    assert plan.inspection.status == "uninstalled"
    assert [step.kind for step in plan.steps] == ["install_schema"]
    assert store.get(Path("^YHMFSchema", (b"YHMFTestProduct",))) is None


def test_unmanaged_data_is_never_adopted_implicitly(store, read_only_store):
    store.set(Path("^YHMFTestProduct", (b"legacy",)), b"unmanaged")
    before = dump_globals(store)
    plan = Database(read_only_store, Registry(Product)).plan_migration(Product)
    assert plan.requires_manual_review
    assert [step.kind for step in plan.steps] == ["manual_change"]
    assert dump_globals(store) == before


def test_destructive_changes_require_review_and_leave_values_intact(store, read_only_store):
    old_db = seed(store)

    class Removed(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)
        sku: str = Field(unique=True)

    plan = Database(read_only_store, Registry(Removed)).plan_migration(Removed)
    assert plan.destructive and plan.requires_manual_review
    assert any(step.kind == "manual_change" and step.path == ("name",) for step in plan.steps)
    assert old_db.get(Product, "P1").name == "Main"


def test_planning_does_not_scan_or_validate_record_values(store, read_only_store, monkeypatch):
    seed(store)
    original_get = read_only_store.get

    def get_metadata_only(path):
        if path.global_name == "^YHMFTestProduct" and path.subscripts:
            raise AssertionError("Planning attempted to read a record")
        return original_get(path)

    monkeypatch.setattr(read_only_store, "get", get_metadata_only)

    class Numeric(Product):
        name: int

    plan = Database(read_only_store, Registry(Numeric)).plan_migration(Numeric)
    assert plan.requires_manual_review
    assert any(step.kind == "validate_records" for step in plan.steps)


def test_unique_index_addition_requires_validation_before_rebuild(store, read_only_store):
    seed(store)

    class Unique(Product):
        name: str = Field(unique=True)

    plan = Database(read_only_store, Registry(Unique)).plan_migration(Unique)
    kinds = [step.kind for step in plan.steps]
    assert kinds.index("validate_uniqueness") < kinds.index("rebuild_index")
    assert any(
        step.kind == "rebuild_index" and step.path == ("name",) and step.unique
        for step in plan.steps
    )


def test_removed_indexes_are_explicit_plan_steps(store, read_only_store):
    seed(store)

    class Unindexed(Product):
        sku: str

    plan = Database(read_only_store, Registry(Unindexed)).plan_migration(Unindexed)
    assert any(step.kind == "drop_index" and step.path == ("sku",) for step in plan.steps)
    assert any(step.kind == "validate_indexes" for step in plan.steps)


def test_changing_existing_defaults_does_not_overwrite_old_values(store, read_only_store):
    class Before(Product):
        name: str = "Old default"

    class After(Product):
        name: str = "New default"

    db = Database(store, Registry(Before))
    db.insert(Before(id="P1", sku="SKU1"))
    plan = Database(read_only_store, Registry(After)).plan_migration(After)
    assert not any(step.kind == "backfill_field" for step in plan.steps)
    assert db.get(Before, "P1").name == "Old default"


def test_default_factories_are_manual_and_never_executed(store, read_only_store):
    seed(store)
    calls = []

    def factory():
        calls.append("called")
        raise RuntimeError("This factory must not execute during planning")

    class Dynamic(Product):
        note: str = Field(default_factory=factory)

    plan = Database(read_only_store, Registry(Dynamic)).plan_migration(Dynamic)
    assert plan.requires_manual_review and calls == []
    assert not any(step.kind == "backfill_field" for step in plan.steps)


def test_defaults_require_an_explicit_serialization_opt_in(store, read_only_store):
    seed(store)

    class Private(Product):
        note: str = "private-default-marker"

    plan = Database(read_only_store, Registry(Private)).plan_migration(Private)
    assert "private-default-marker" not in repr(plan)
    assert "private-default-marker" not in plan.to_json()
    assert "private-default-marker" in plan.to_json(include_defaults=True)


def test_nested_backfills_target_existing_owners_without_materializing_children(
    store, read_only_store
):
    class Lot(Model):
        code: str = Field(primary_key=True)

    class Warehouse(Model):
        __global__ = "^YHMFTestWarehouse"
        id: int = Field(primary_key=True)
        lots: Children[Lot] = ChildField(key="code")

    class NewLot(Lot):
        note: str | None = Field(default=None, index=True)

    class NewWarehouse(Warehouse):
        lots: Children[NewLot] = ChildField(key="code")

    db = Database(store, Registry(Warehouse))
    db.insert(Warehouse(id=1, lots={"L1": Lot(code="L1")}))
    plan = Database(read_only_store, Registry(NewWarehouse)).plan_migration(NewWarehouse)
    steps = plan.to_dict()["steps"]
    backfill = next(step for step in steps if step["kind"] == "backfill_field")
    assert backfill["path"] == ["lots", "note"] and backfill["scope"] == "existing_owners"
    assert "L1" not in plan.to_json()
    assert list(db.get(Warehouse, 1).lots) == ["L1"]


def test_nullable_reference_addition_includes_reverse_index_validation(store, read_only_store):
    class Order(Model):
        __global__ = "^YHMFTestOrder"
        id: int = Field(primary_key=True)

    class Referenced(Order):
        product: Reference[Product] | None = None

    db = Database(store, Registry(Order, Product))
    db.insert(Order(id=1))
    plan = Database(read_only_store, Registry(Referenced, Product)).plan_migration(Referenced)
    assert not plan.requires_manual_review
    assert any(step.kind == "rebuild_references" for step in plan.steps)
    assert any(step.kind == "validate_indexes" for step in plan.steps)


def test_transaction_bound_planning_handles_expire(read_only_store):
    db = Database(read_only_store, Registry(Product))
    handle = db.atomic(lambda tx: tx)
    with pytest.raises(TransactionError):
        handle.plan_migration(Product)


def test_add_field_plan_matches_the_review_format_fixture(store, read_only_store):
    seed(store)
    plan = Database(read_only_store, Registry(ProductV2)).plan_migration(ProductV2)
    fixture = FilePath(__file__).parent / "fixtures/add-field-migration-plan.json"
    assert plan.to_dict() == json.loads(fixture.read_text())
