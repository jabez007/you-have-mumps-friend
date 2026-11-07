# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal, localcontext

import pytest
from pydantic import ValidationError, model_validator

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    Model,
    Patch,
    Reference,
    Registry,
    Transform,
)
from mccann_hub.yhmf.errors import (
    MutationError,
    SchemaError,
    StorageError,
    UniqueViolation,
)
from mccann_hub.yhmf.storage import subtree_nodes
from tests.test_partial_mutations import ObservedStore


class Quantity(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal(0), ge=0)
    label: str = "stock"


class Bin(Model):
    id: str = Field(primary_key=True)
    items: Children[Quantity] = ChildField(key="id")


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    bins: Children[Bin] = ChildField(key="id")
    total: Decimal = Field(sum_of="bins.items.quantity", index=True, ge=0)


def warehouse(key="W", size=3):
    return Warehouse(
        id=key,
        bins={
            "A": Bin(
                id="A",
                items={str(i): Quantity(id=str(i), quantity=Decimal("1.25")) for i in range(size)},
            )
        },
    )


def setup(store, size=3):
    db = Database(store, Registry(Warehouse))
    db.insert(warehouse(size=size))
    return db


def test_whole_writes_derive_indexed_totals_after_validation_and_preserve_input_values(store):
    db = Database(store, Registry(Warehouse))
    incoming = warehouse()
    assert incoming.total == 0
    persisted = db.insert(incoming)
    assert persisted.total == Decimal("3.75") and incoming.total == 0
    assert db.get(Warehouse, "W").total == persisted.total
    assert db.query(Warehouse).where(total=Decimal("3.750")).all()[0].id == "W"
    persisted.bins["A"].items["0"].quantity = Decimal(2)
    assert db.save(persisted).total == Decimal("4.50")
    assert db.check_indexes(Warehouse).issues == ()


def test_owned_scalar_changes_insertions_and_deletions_update_one_indexed_total(store):
    db = setup(store)
    db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(2)})
    assert db.get(Warehouse, "W").total == Decimal("4.50")
    assert db.query(Warehouse).where(total=Decimal("3.75")).all() == []
    db.insert_child(
        Warehouse,
        "W",
        collection=("bins", "A", "items"),
        record=Quantity(id="new", quantity=Decimal(".5")),
    )
    assert db.get(Warehouse, "W").total == 5
    db.delete_child(Warehouse, "W", at=("bins", "A", "items", "0"))
    assert db.get(Warehouse, "W").total == 3
    db.delete_child(Warehouse, "W", at=("bins", "A"))
    assert db.get(Warehouse, "W").total == 0
    assert db.check_indexes(Warehouse).issues == ()


def test_net_zero_group_touches_neither_total_nor_its_index(store):
    observed = ObservedStore(store)
    db = setup(observed)
    observed.operations.clear()
    db.patch_many(
        [
            Patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(2)}),
            Patch(
                Warehouse, "W", at=("bins", "A", "items", "1"), changes={"quantity": Decimal(".5")}
            ),
        ]
    )
    root = db.registry.spec(Warehouse).path("W")
    assert not any(
        path == root.child(b"total")
        or path.global_name == "^YHMFIndex"
        and path.subscripts[1] == b"total"
        for _, path in observed.operations
    )
    assert db.get(Warehouse, "W").total == Decimal("3.75")


def test_partial_rollup_cost_does_not_grow_with_unrelated_siblings(store):
    observed = ObservedStore(store)
    db = setup(observed, size=1000)
    observed.operations.clear()
    db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(2)})
    assert not any(b"s:999" in path.subscripts for _, path in observed.operations)
    assert len(observed.operations) < 100
    root = db.registry.spec(Warehouse).path("W")
    assert (
        sum(op == "get" and path == root.child(b"total") for op, path in observed.operations) == 1
    )
    assert (
        sum(op == "set" and path == root.child(b"total") for op, path in observed.operations) == 1
    )
    assert db.get(Warehouse, "W").total == Decimal("1250.75")


def test_explicit_total_patch_fails_before_writes_and_group_exchanges_unique_totals(store):
    class Unique(Warehouse):
        total: Decimal = Field(sum_of="bins.items.quantity", unique=True)

    observed = ObservedStore(store)
    db = Database(observed, Registry(Unique))
    for key, quantity in [("A", Decimal(1)), ("B", Decimal(2))]:
        db.insert(
            Unique(
                id=key, bins={"X": Bin(id="X", items={"Q": Quantity(id="Q", quantity=quantity)})}
            )
        )
    observed.operations.clear()
    with pytest.raises(MutationError, match="read-only"):
        db.patch(Unique, "A", changes={"total": Decimal(2)})
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)
    with pytest.raises(UniqueViolation):
        db.patch(Unique, "A", at=("bins", "X", "items", "Q"), changes={"quantity": Decimal(2)})
    assert db.get(Unique, "A").total == 1
    db.patch_many(
        [
            Patch(Unique, "A", at=("bins", "X", "items", "Q"), changes={"quantity": Decimal(2)}),
            Patch(Unique, "B", at=("bins", "X", "items", "Q"), changes={"quantity": Decimal(1)}),
        ]
    )
    assert db.get(Unique, "A").total == 2 and db.get(Unique, "B").total == 1
    assert db.check_indexes(Unique).issues == ()


@pytest.mark.parametrize(
    "value",
    [Decimal("1E18"), Decimal(".1234567890123456789"), Decimal("1E-19"), Decimal("1E1000000000")],
)
def test_stored_sum_numeric_domain_is_explicit_and_failure_rolls_back(store, value):
    db = setup(store)
    with pytest.raises(StorageError, match="decimal-sum-v1"):
        db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": value})
    assert db.get(Warehouse, "W").total == Decimal("3.75")
    assert db.check_indexes(Warehouse).issues == ()


def test_exact_sum_is_independent_of_decimal_context_and_canonical_zero_scale(store):
    db = setup(store, size=0)
    with localcontext() as context:
        context.prec = 2
        db.insert_child(
            Warehouse,
            "W",
            collection=("bins", "A", "items"),
            record=Quantity(id="tiny", quantity=Decimal(".123456789012345678")),
        )
        db.insert_child(
            Warehouse,
            "W",
            collection=("bins", "A", "items"),
            record=Quantity(id="large", quantity=Decimal("999999999999999999")),
        )
        assert db.get(Warehouse, "W").total == Decimal("999999999999999999.123456789012345678")
        db.delete_child(Warehouse, "W", at=("bins", "A", "items", "large"))
        db.delete_child(Warehouse, "W", at=("bins", "A", "items", "tiny"))
    assert db.get(Warehouse, "W").total.as_tuple() == Decimal(0).as_tuple()


def test_corrupt_total_is_detected_during_full_reads_and_index_rebuild_validation(store):
    db = setup(store)
    root = db.registry.spec(Warehouse).path("W")
    store.set(root.child(b"total"), b"d:99")
    with pytest.raises(StorageError, match="disagrees"):
        db.get(Warehouse, "W")
    status = db.begin_index_rebuild(Warehouse, plan=db.plan_index_rebuild(Warehouse))
    with pytest.raises(StorageError, match="disagrees"):
        db.advance_index_rebuild(status.migration_id, Warehouse)
    assert db.abort_migration(status.migration_id).phase == "aborted"


def test_leaf_model_validators_contribute_their_validated_quantity_changes(store):
    class Adjusted(Quantity):
        @model_validator(mode="after")
        def adjust(self):
            if self.label == "reset":
                object.__setattr__(self, "quantity", Decimal(0))
            return self

    class Bucket(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        items: Children[Adjusted] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity", index=True)

    db = Database(store, Registry(Bucket))
    db.insert(Bucket(id="W", items={"Q": Adjusted(id="Q", quantity=Decimal(5))}))
    db.patch(Bucket, "W", at=("items", "Q"), changes={"label": "reset"})
    assert db.get(Bucket, "W").total == 0
    assert db.check_indexes(Bucket).issues == ()


def test_cascade_child_deletion_updates_surviving_root_and_total_index(store):
    class Target(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class Dependent(Quantity):
        product: Reference[Target] = Field(on_delete="cascade")

    class Root(Model):
        __global__ = "^YHMFTestWarehouse"
        id: str = Field(primary_key=True)
        items: Children[Dependent] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity", index=True)

    db = Database(store, Registry(Target, Root))
    db.insert(Target(id="P"))
    db.insert(
        Root(
            id="W",
            items={"Q": Dependent(id="Q", quantity=Decimal(5), product=Reference[Target](key="P"))},
        )
    )
    db.delete(Target, "P")
    assert db.get(Root, "W").total == 0 and db.get(Root, "W").items == {}
    assert db.check_indexes(Root).issues == ()
    assert db.delete(Root, "W")


def test_adding_sum_requires_explicit_transform_and_verifies_its_materialized_total(store):
    class Before(Model):
        __global__ = Warehouse.__global__
        id: str = Field(primary_key=True)
        bins: Children[Bin] = ChildField(key="id")

    record = warehouse()
    old = Database(store, Registry(Before))
    old.insert(Before.model_validate(record.model_dump(exclude={"total"})))
    db = Database(store, Registry(Warehouse))
    assert not db.plan_migration(Warehouse).execution_supported
    convert = Transform(
        "test.warehouse.total.v1", lambda value: Warehouse.model_validate(value.model_dump())
    )
    plan = db.plan_migration(Warehouse, transform=convert)
    assert plan.execution_supported
    status = db.begin_migration(Before, Warehouse, plan=plan, transform=convert)
    for _ in range(100):
        status = db.advance_migration(
            status.migration_id, Before, Warehouse, batch_size=1, transform=convert
        )
        if status.phase == "complete":
            break
    assert status.phase == "complete"
    assert db.get(Warehouse, "W").total == Decimal("3.75")
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.parametrize("path", ["bins.items.missing", "id", "bins.items.id", "bins.total", "", 1])
def test_unsupported_sum_paths_fail_at_registration(store, path):
    class Bad(Warehouse):
        total: Decimal = Field(sum_of=path)

    with pytest.raises(SchemaError):
        Registry(Bad)


def test_signed_sources_and_zero_scale_are_exact(store):
    class Signed(Quantity):
        quantity: Decimal

    class Root(Model):
        __global__ = Warehouse.__global__
        id: str = Field(primary_key=True)
        items: Children[Signed] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity")

    db = Database(store, Registry(Root))
    db.insert(
        Root(
            id="W",
            items={
                "A": Signed(id="A", quantity=Decimal("-999999999999999999")),
                "B": Signed(id="B", quantity=Decimal("999999999999999999")),
            },
        )
    )
    assert db.get(Root, "W").total == 0
    db.delete_child(Root, "W", at=("items", "B"))
    assert db.get(Root, "W").total == Decimal("-999999999999999999")


def test_custom_ancestor_hooks_are_rejected_instead_of_bypassed(store):
    class Invalid(Warehouse):
        @model_validator(mode="after")
        def arbitrary_root_rule(self):
            return self

    with pytest.raises(SchemaError, match="local validation"):
        Registry(Invalid)

    class Parent(Bin):
        @model_validator(mode="after")
        def arbitrary_parent_rule(self):
            return self

    class InvalidParent(Warehouse):
        bins: Children[Parent] = ChildField(key="id")

    with pytest.raises(SchemaError, match="local validation"):
        Registry(InvalidParent)


def test_set_null_leaf_validator_quantity_change_updates_total(store):
    class Target(Model):
        __global__ = "^YHMFTestProduct"
        id: str = Field(primary_key=True)

    class Dependent(Quantity):
        product: Reference[Target] | None = Field(default=None, on_delete="set_null")

        @model_validator(mode="after")
        def clear_unlinked(self):
            if self.product is None:
                object.__setattr__(self, "quantity", Decimal(0))
            return self

    class Root(Model):
        __global__ = Warehouse.__global__
        id: str = Field(primary_key=True)
        items: Children[Dependent] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity", index=True)

    db = Database(store, Registry(Target, Root))
    db.insert(Target(id="P"))
    db.insert(
        Root(
            id="W",
            items={"Q": Dependent(id="Q", quantity=Decimal(5), product=Reference[Target](key="P"))},
        )
    )
    db.delete(Target, "P")
    assert db.get(Root, "W").total == 0
    assert db.get(Root, "W").items["Q"].product is None
    assert db.check_indexes(Root).issues == ()


def test_changed_dependency_requires_reviewed_transform_and_unknown_codec_is_unsupported(store):
    import json

    from mccann_hub.yhmf import SchemaSnapshot, compare_schemas
    from mccann_hub.yhmf.codecs import canonical_json

    class OtherQuantity(Quantity):
        alternate: Decimal = Decimal(2)

    class Root(Model):
        __global__ = Warehouse.__global__
        id: str = Field(primary_key=True)
        items: Children[OtherQuantity] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity", index=True)

    class Changed(Root):
        total: Decimal = Field(sum_of="items.alternate", index=True)

    before = SchemaSnapshot(Registry(Root).spec(Root).snapshot)
    after = SchemaSnapshot(Registry(Changed).spec(Changed).snapshot)
    changes = compare_schemas(before, after)
    assert {c.kind for c in changes} >= {"rollup_changed", "index_rebuild_required"}
    assert next(c for c in changes if c.kind == "rollup_changed").requires_manual_review
    document = json.loads(after.canonical)
    document["declaration"]["properties"]["total"]["yhmf"]["rollup_codec"] = "unknown-v2"
    assert any(
        c.kind == "unsupported_schema"
        for c in compare_schemas(before, SchemaSnapshot(canonical_json(document)))
    )


def test_stored_sum_schema_matches_golden_and_is_deterministic():
    import json
    from pathlib import Path as FilePath

    from mccann_hub.yhmf.codecs import canonical_json

    spec = Registry(Warehouse).spec(Warehouse)
    fixture = FilePath(__file__).with_name("fixtures") / "stored-sum-v1.json"
    assert spec.snapshot == canonical_json(json.loads(fixture.read_text()))
    assert spec.snapshot == Registry(Warehouse).spec(Warehouse).snapshot


@pytest.mark.adversarial
def test_generated_mutations_preserve_total_and_independent_physical_index():
    import hashlib
    import json

    from hypothesis import given, settings
    from hypothesis import strategies as st

    from mccann_hub.yhmf import MemoryStore, Path
    from mccann_hub.yhmf.errors import AlreadyExists, NotFound

    @settings(max_examples=20, deadline=None)
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["insert", "patch", "delete", "invalid"]),
                st.integers(0, 4),
                st.integers(-2, 1000),
            ),
            min_size=1,
            max_size=20,
        )
    )
    def check(operations):
        store = MemoryStore()
        db = setup(store, size=0)
        expected = {}
        for operation, key, number in operations:
            key = str(key)
            value = Decimal((int(number < 0), tuple(map(int, str(abs(number)))), -3))
            try:
                if operation == "insert":
                    db.insert_child(
                        Warehouse,
                        "W",
                        collection=("bins", "A", "items"),
                        record=Quantity(id=key, quantity=value),
                    )
                    expected[key] = value
                elif operation == "patch":
                    db.patch(
                        Warehouse, "W", at=("bins", "A", "items", key), changes={"quantity": value}
                    )
                    expected[key] = value
                elif operation == "delete":
                    db.delete_child(Warehouse, "W", at=("bins", "A", "items", key))
                    expected.pop(key)
                else:
                    with pytest.raises(MutationError):
                        db.patch(Warehouse, "W", changes={"total": value})
            except (AlreadyExists, NotFound, ValidationError):
                pass
            record = db.get(Warehouse, "W")
            with localcontext() as context:
                context.prec = 80
                total = sum(expected.values(), Decimal(0))
                normalized = total.normalize()
            assert record.total == total
            assert {key: item.quantity for key, item in record.bins["A"].items.items()} == expected
            sign, digits, exponent = normalized.as_tuple()
            token = (
                "d:0"
                if total == 0
                else f"d:{'-' if sign else ''}{''.join(map(str, digits))}E{exponent}"
            )
            location = b'["total"]'
            path = Path(
                "^YHMFIndex",
                (
                    b"YHMFTestWarehouse",
                    b"total",
                    b"v:" + json.dumps(token).encode(),
                    b"s:W",
                    hashlib.sha256(location).hexdigest().encode(),
                ),
            )
            assert subtree_nodes(store, Path("^YHMFIndex"), limit=100) == {path: location}

    check()


@pytest.mark.native
def test_native_sum_restart_and_late_failure_rollback_indexes_and_source(store):
    from mccann_hub.yhmf import NativeStore

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")

    class Interrupted(ObservedStore):
        action = None

        def set(self, path, value):
            super().set(path, value)
            if self.action == "restart" and path.global_name == "^YHMFIndex":
                self.action = None
                raise yottadb.YDBTPRestart()
            if (
                self.action == "abort"
                and path.global_name == Warehouse.__global__
                and path.subscripts[-1] == b"total"
            ):
                self.action = None
                raise RuntimeError("late sum failure")

    observed = Interrupted(store)
    db = setup(observed)
    observed.action = "restart"
    db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(2)})
    assert db.get(Warehouse, "W").total == Decimal("4.5")
    observed.action = "abort"
    with pytest.raises(RuntimeError, match="late sum failure"):
        db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(3)})
    assert db.get(Warehouse, "W").total == Decimal("4.5")
    assert db.get(Warehouse, "W").bins["A"].items["0"].quantity == 2
    assert db.check_indexes(Warehouse).issues == ()


def _native_case(store, mode):
    from tests.native_rollups import UniqueWarehouse

    if mode == "unique":
        db = Database(store, Registry(UniqueWarehouse))
        for key, value in [("A", Decimal(1)), ("B", Decimal(2))]:
            db.insert(
                UniqueWarehouse(
                    id=key, bins={"X": Bin(id="X", items={"Q": Quantity(id="Q", quantity=value)})}
                )
            )
        commands = [("unique", "A", "Q", "3"), ("unique", "B", "Q", "3")]
        expected = ["committed", "UniqueViolation"]
    else:
        db = setup(store)
        commands = [
            ("patch", "W", "0", "2"),
            ("patch", "W", "0" if mode == "same_child" else "1", "3"),
        ]
        expected = ["committed", "committed"]
        if mode == "delete_first":
            commands[0] = ("delete", "W", "0", "0")
            expected[1] = "NotFound"
        elif mode == "patch_first":
            commands[1] = ("delete", "W", "0", "0")
    return db, commands, expected


@pytest.mark.native
@pytest.mark.parametrize(
    "mode", ["separate_children", "same_child", "unique", "delete_first", "patch_first"]
)
def test_native_concurrent_sources_totals_and_indexes_remain_atomic(store, mode):
    import subprocess
    import sys

    from mccann_hub.yhmf import NativeStore
    from tests.native_rollups import UniqueWarehouse
    from tests.test_native_schema_safety import ready

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native multi-process transactions")
    db, commands, expected = _native_case(store, mode)
    workers = []
    try:
        for command in commands:
            worker = subprocess.Popen(
                [sys.executable, "-m", "tests.native_rollups", *command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(worker)
            ready(worker)
        statuses = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0, error
            statuses.append(output.strip())
        assert statuses == expected
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    if mode == "unique":
        assert db.get(UniqueWarehouse, "A").total == 3
        assert db.get(UniqueWarehouse, "B").total == 2
        assert db.check_indexes(UniqueWarehouse).issues == ()
    elif mode in {"delete_first", "patch_first"}:
        assert db.scan(Warehouse) == []
        assert db.check_indexes(Warehouse).issues == ()
    else:
        assert db.get(Warehouse, "W").total == Decimal("5.5" if mode == "same_child" else "6.25")
        assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.parametrize("damage", [None, b"f:3.75", b"d:1E+1000000000"])
def test_invalid_stored_total_prevents_partial_changes_before_any_write(store, damage):
    observed = ObservedStore(store)
    db = setup(observed)
    path = db.registry.spec(Warehouse).path("W").child(b"total")
    if damage is None:
        store.delete_tree(path)
    else:
        store.set(path, damage)
    observed.operations.clear()
    with pytest.raises((StorageError, ValidationError)):
        db.patch(Warehouse, "W", at=("bins", "A", "items", "0"), changes={"quantity": Decimal(2)})
    assert not any(op in {"set", "delete"} for op, _ in observed.operations)
