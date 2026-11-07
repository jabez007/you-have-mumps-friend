# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mccann_hub.yhmf import Database, MemoryStore, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import StorageError
from tests.adversarial_support import dump
from tests.mutation_models import Lot, Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore
from tests.test_schema_contracts import seed_legacy


@pytest.mark.parametrize("legacy", [False, True])
def test_one_field_save_changes_only_that_node_and_noop_writes_nothing(store, legacy):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    if legacy:
        seed_legacy(store, warehouse(3))
    else:
        db.insert(warehouse(3))
    value = db.get(Warehouse, "W")
    value.bins["A"].lots["1"].note = "changed"
    observed.operations.clear()
    db.save(value)
    writes = [(kind, path) for kind, path in observed.operations if kind in {"set", "delete"}]
    assert writes == [
        ("set", Path(Warehouse.__global__, (b"s:W", b"bins", b"s:A", b"lots", b"s:1", b"note")))
    ]
    observed.operations.clear()
    db.save(db.get(Warehouse, "W"))
    assert not any(kind in {"set", "delete"} for kind, _ in observed.operations)


def test_replacement_removes_raw_nodes_even_when_their_parent_has_retained_children(store):
    db = Database(store, Registry(Product, Warehouse))
    original = warehouse(3)
    db.insert(original)
    root = db.registry.spec(Warehouse).path("W")
    expected = dump(store, (Warehouse.__global__,))
    store.set(root, b"unexpected root value")
    store.set(root.child(b"%m"), b"unexpected metadata parent")
    store.set(root.child(b"unknown", b"deep"), b"raw")
    store.set(root.child(b"name", b"raw"), b"child below a scalar")
    db.save(original)
    assert dump(store, (Warehouse.__global__,)) == expected
    assert db.get(Warehouse, "W") == original


def test_replacement_removes_owned_subtrees_and_nulls_without_rewriting_siblings(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    value = warehouse(3)
    db.insert(value)
    del value.bins["A"].lots["0"]
    value.address = None
    observed.operations.clear()
    db.save(value)
    assert db.get(Warehouse, "W") == value
    writes = [path for kind, path in observed.operations if kind in {"set", "delete"}]
    assert not any(path.subscripts[4:5] in {(b"s:1",), (b"s:2",)} for path in writes)
    assert db.check_indexes(Warehouse).issues == ()


def test_replacement_write_fault_rolls_back_physical_and_secondary_changes(store, monkeypatch):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(warehouse(2))
    value = db.get(Warehouse, "W")
    value.bins["A"].lots["0"].quantity = Decimal(4)
    del value.bins["A"].lots["1"]
    roots = (Warehouse.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef")
    before, real_set = dump(store, roots), store.set

    def fail_after_write(path, value):
        real_set(path, value)
        raise RuntimeError("interrupted difference")

    with monkeypatch.context() as patch:
        patch.setattr(store, "set", fail_after_write)
        with pytest.raises(RuntimeError, match="interrupted"):
            db.save(value)
    assert dump(store, roots) == before


@pytest.mark.adversarial
@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 20), st.booleans()), max_size=8))
def test_generated_difference_matches_full_delete_and_insert(values):
    _assert_full_replacement_equivalence(MemoryStore(), values)


@pytest.mark.native
@pytest.mark.adversarial
@settings(
    max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.tuples(st.integers(0, 20), st.booleans()), max_size=8))
def test_native_generated_difference_matches_full_delete_and_insert(store, values):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires a native property workload")
    for name in (
        Product.__global__,
        Warehouse.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    ):
        store.delete_tree(Path(name))
    _assert_full_replacement_equivalence(store, values)


def _assert_full_replacement_equivalence(store, values):
    db = Database(store, Registry(Product, Warehouse))
    for key in ("P", "Q"):
        db.insert(Product(id=key, name=key))
    db.insert(warehouse(5))
    target = warehouse(0)
    target.bins["A"].lots = {
        str(i): Lot(
            id=str(i),
            quantity=Decimal(amount),
            serial=f"S{i}",
            product=Reference[Product](key="P" if use_p else "Q"),
        )
        for i, (amount, use_p) in enumerate(values)
    }
    db.save(target)
    roots = (Warehouse.__global__, "^YHMFIndex", "^YHMFUnique", "^YHMFRef")
    diff = dump(store, roots)
    db.atomic(lambda tx: (tx.delete(Warehouse, "W"), tx.insert(target)))
    assert dump(store, roots) == diff
    assert db.check_indexes(Warehouse).issues == ()


def test_oversized_raw_subtree_aborts_before_any_replacement_writes(store, monkeypatch):
    from mccann_hub.yhmf import replacement

    db = Database(store, Registry(Product, Warehouse))
    value = warehouse(1)
    db.insert(value)
    root = db.registry.spec(Warehouse).path("W")
    for number in range(20):
        store.set(root.child(b"unknown", str(number).encode()), b"raw")
    before = dump(store, (Warehouse.__global__, "^YHMFIndex"))
    monkeypatch.setattr(replacement, "MAX_RECORD_NODES", 25)
    value.name = "No"
    with pytest.raises(StorageError, match="traversal budget"):
        db.save(value)
    assert dump(store, (Warehouse.__global__, "^YHMFIndex")) == before
