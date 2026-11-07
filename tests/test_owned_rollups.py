# SPDX-License-Identifier: GPL-3.0-or-later
import json
from decimal import Decimal
from pathlib import Path as FilePath

import pytest
from pydantic import model_validator

from mccann_hub.yhmf import ChildField, Children, Database, Field, Model, Patch, Registry
from mccann_hub.yhmf.errors import MutationError, SchemaError, StorageError, UniqueViolation
from tests.test_partial_mutations import ObservedStore
from tests.test_stored_rollups import Quantity


class Location(Model):
    id: str = Field(primary_key=True)
    items: Children[Quantity] = ChildField(key="id")
    total: Decimal = Field(sum_of="items.quantity", index=True, ge=0)


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    locations: Children[Location] = ChildField(key="id")
    total: Decimal = Field(sum_of="locations.items.quantity", index=True, ge=0)


def setup(store, size=1):
    db = Database(store, Registry(Warehouse))
    db.insert(
        Warehouse(
            id="W",
            locations={
                key: Location(
                    id=key,
                    items={str(i): Quantity(id=str(i), quantity=Decimal(2)) for i in range(size)},
                )
                for key in ("A", "B")
            },
        )
    )
    return db


def test_owned_totals_initialize_verify_and_update_at_each_surviving_level(store):
    db = setup(store)
    assert db.get(Warehouse, "W").total == 4
    assert db.get(Warehouse, "W", at=("locations", "A")).total == 2
    db.patch(Warehouse, "W", at=("locations", "A", "items", "0"), changes={"quantity": Decimal(3)})
    assert db.get(Warehouse, "W").total == 5
    assert db.query(Warehouse).where(**{"locations.total": Decimal(3)}).occurrences()[0].at == (
        "locations",
        "A",
    )
    db.insert_child(
        Warehouse,
        "W",
        collection=("locations",),
        record=Location(id="C", items={"X": Quantity(id="X", quantity=Decimal(7))}),
    )
    assert db.get(Warehouse, "W", at=("locations", "C")).total == 7
    assert db.get(Warehouse, "W").total == 12
    db.delete_child(Warehouse, "W", at=("locations", "C"))
    assert db.get(Warehouse, "W").total == 5
    assert db.check_indexes(Warehouse).issues == ()
    root = db.registry.spec(Warehouse).path("W")
    store.set(root.child(b"locations", b"s:A", b"total"), b"d:99")
    with pytest.raises(StorageError, match="Stored sum"):
        db.get(Warehouse, "W")
    with pytest.raises(StorageError, match="Stored sum"):
        db.get(Warehouse, "W", at=("locations", "A"))


def test_owned_partial_deltas_do_not_visit_siblings_and_groups_coalesce(store):
    observed = ObservedStore(store)
    db = setup(observed, 100)
    observed.operations.clear()
    db.patch_many(
        [
            Patch(
                Warehouse,
                "W",
                at=("locations", "A", "items", "0"),
                changes={"quantity": Decimal(3)},
            ),
            Patch(
                Warehouse,
                "W",
                at=("locations", "A", "items", "1"),
                changes={"quantity": Decimal(1)},
            ),
        ]
    )
    assert not any(path.subscripts[-1:] == (b"total",) for _, path in observed.operations)
    assert not any(
        b"s:99" in path.subscripts or b"s:B" in path.subscripts for _, path in observed.operations
    )
    observed.operations.clear()
    db.patch(Warehouse, "W", at=("locations", "A", "items", "0"), changes={"quantity": Decimal(4)})
    for parts in [(b"total",), (b"locations", b"s:A", b"total")]:
        path = db.registry.spec(Warehouse).path("W").child(*parts)
        assert sum(op == "get" and p == path for op, p in observed.operations) == 1
        assert sum(op == "set" and p == path for op, p in observed.operations) == 1
    with pytest.raises(MutationError, match="read-only"):
        db.patch(Warehouse, "W", at=("locations", "A"), changes={"total": Decimal(0)})
    assert db.check_indexes(Warehouse).issues == ()


def test_owned_sums_cannot_bypass_ancestor_validation_or_depend_on_derived_sums():
    class Validated(Warehouse):
        @model_validator(mode="after")
        def check(self):
            return self

    class Derived(Warehouse):
        total: Decimal = Field(sum_of="locations.total")

    for owner in (Validated, Derived):
        with pytest.raises(SchemaError):
            Registry(owner)


def test_embedded_nullable_sum_owner_without_root_sum_maintains_indexes(store):
    class Summary(Model):
        items: Children[Quantity] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.quantity", index=True)

    class Root(Model):
        __global__ = Warehouse.__global__
        id: str = Field(primary_key=True)
        summary: Summary | None = None

    db = Database(store, Registry(Root))
    db.insert(Root(id="empty"))
    db.insert(Root(id="W", summary=Summary(items={"Q": Quantity(id="Q", quantity=Decimal(2))})))
    db.patch(Root, "W", at=("summary", "items", "Q"), changes={"quantity": Decimal(4)})
    assert db.get(Root, "W").summary.total == 4
    assert db.query(Root).where(**{"summary.total": Decimal(4)}).all()[0].id == "W"
    assert db.check_indexes(Root).issues == ()

    class Invalid(Root):
        @model_validator(mode="after")
        def validate_root(self):
            return self

    with pytest.raises(SchemaError, match="local validation"):
        Registry(Invalid)


def test_owned_sum_unique_failure_rolls_back_source_and_all_totals(store):
    class UniqueLocation(Location):
        total: Decimal = Field(sum_of="items.quantity", unique=True)

    class UniqueWarehouse(Warehouse):
        locations: Children[UniqueLocation] = ChildField(key="id")

    db = Database(store, Registry(UniqueWarehouse))
    db.insert(
        UniqueWarehouse(
            id="W",
            locations={
                key: UniqueLocation(id=key, items={"Q": Quantity(id="Q", quantity=Decimal(amount))})
                for key, amount in (("A", 2), ("B", 3))
            },
        )
    )
    with pytest.raises(UniqueViolation):
        db.patch(
            UniqueWarehouse,
            "W",
            at=("locations", "A", "items", "Q"),
            changes={"quantity": Decimal(3)},
        )
    record = db.get(UniqueWarehouse, "W")
    assert record.total == 5 and record.locations["A"].total == 2
    assert record.locations["A"].items["Q"].quantity == 2
    assert db.check_indexes(UniqueWarehouse).issues == ()


def test_owned_sum_canonical_golden():
    from mccann_hub.yhmf.codecs import canonical_json

    fixture = FilePath(__file__).with_name("fixtures") / "owned-sum-v1.json"
    assert Registry(Warehouse).spec(Warehouse).snapshot == canonical_json(
        json.loads(fixture.read_text())
    )


@pytest.mark.native
@pytest.mark.parametrize(
    "mode", ["same_location", "different_locations", "delete_first", "patch_first"]
)
def test_native_owned_sum_concurrent_changes_remain_consistent(store, mode):
    import subprocess
    import sys

    from mccann_hub.yhmf import NativeStore
    from tests.test_native_schema_safety import ready

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native multi-process transactions")
    db = setup(store, 2)
    commands = [
        ("patch", "A", "0", "3"),
        ("patch", "B" if mode == "different_locations" else "A", "1", "4"),
    ]
    expected = ["committed", "committed"]
    if mode == "delete_first":
        commands[0] = ("delete", "A", "0", "0")
        expected[1] = "NotFound"
    elif mode == "patch_first":
        commands[1] = ("delete", "A", "0", "0")
    workers = []
    try:
        for command in commands:
            worker = subprocess.Popen(
                [sys.executable, "-m", "tests.native_owned_rollups", *command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(worker)
            ready(worker)
        results = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0, error
            results.append(output.strip())
        assert results == expected
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    record = db.get(Warehouse, "W")
    assert record.total == (4 if "first" in mode else 11)
    for location in record.locations.values():
        assert location.total == sum((q.quantity for q in location.items.values()), Decimal(0))
    assert db.check_indexes(Warehouse).issues == ()


@pytest.mark.native
def test_native_owned_sum_forced_restart_and_late_rollback(store):
    from mccann_hub.yhmf import NativeStore

    if not isinstance(store, NativeStore):
        pytest.skip("Requires native restart")
    yottadb = pytest.importorskip("yottadb")
    observed = ObservedStore(store)
    db = setup(observed)
    attempts = []

    def update(tx):
        attempts.append(True)
        tx.patch(
            Warehouse, "W", at=("locations", "A", "items", "0"), changes={"quantity": Decimal(3)}
        )
        if len(attempts) == 1:
            raise yottadb.YDBTPRestart()

    db.atomic(update)
    assert len(attempts) == 2
    assert db.get(Warehouse, "W").total == 5

    def abort(tx):
        tx.patch(
            Warehouse, "W", at=("locations", "A", "items", "0"), changes={"quantity": Decimal(9)}
        )
        raise RuntimeError("after owned sums and indexes")

    with pytest.raises(RuntimeError, match="after owned sums"):
        db.atomic(abort)
    assert db.get(Warehouse, "W").total == 5
    assert db.get(Warehouse, "W", at=("locations", "A")).total == 3
    assert db.check_indexes(Warehouse).issues == ()
