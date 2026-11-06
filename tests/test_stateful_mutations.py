# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated operations against plain Python state, on both storage adapters."""

from decimal import Decimal

import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test
from pydantic import ValidationError

from mccann_hub.yhmf import Database, Path, Reference, Registry
from mccann_hub.yhmf.errors import AlreadyExists, NotFound, ReferenceViolation, UniqueViolation
from tests.adversarial_support import audit, dump, sequence_settings
from tests.mutation_models import Bin, Lot, Product, Warehouse

pytestmark = pytest.mark.adversarial
ROOTS = (
    "^YHMFTestWarehouse",
    "^YHMFTestProduct",
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
)
root_ids = st.sampled_from(["W0", "W1"])
child_ids = st.sampled_from(["0", "01", "雪"])
bin_ids = st.sampled_from(["A", "B"])
texts = st.sampled_from(["", "雪", "a\x00b", '"quoted"', "line\nend"])


def perform(error, callback):
    if error is None:
        callback()
    else:
        with pytest.raises(error):
            callback()


def plain_lot():
    return {"quantity": 0, "note": "", "serial": None, "product": None}


class MutationMachine(RuleBasedStateMachine):
    def __init__(self, store):
        super().__init__()
        self.store, self.state, self.products = store, {}, {"P0", "P1"}
        for root in ROOTS:
            store.delete_tree(Path(root))
        self.db = Database(store, Registry(Product, Warehouse))
        for product in self.products:
            self.db.insert(Product(id=product, name=product))
        self.insert("W0", "Main")
        self.insert_child("W0", "A", "0")
        self.insert_child("W0", "B", "01")
        self.patch_reference("W0", "A", "0", "P0")

    def missing(self, root, bin_id, child=None):
        if root not in self.state:
            return True
        return child is not None and child not in self.state[root]["bins"][bin_id]

    @rule(root=root_ids, name=texts)
    def insert(self, root, name):
        error = AlreadyExists if root in self.state else None
        perform(
            error,
            lambda: self.db.insert(
                Warehouse(id=root, name=name, bins={b: Bin(id=b) for b in ("A", "B")})
            ),
        )
        if error is None:
            self.state[root] = {"name": name, "bins": {"A": {}, "B": {}}}

    @rule(root=root_ids, name=texts)
    def replace(self, root, name):
        value = Warehouse(id=root, name=name, bins={b: Bin(id=b) for b in ("A", "B")})
        self.db.save(value)
        self.state[root] = {"name": name, "bins": {"A": {}, "B": {}}}

    @rule(root=root_ids)
    def delete(self, root):
        assert self.db.delete(Warehouse, root) == (root in self.state)
        self.state.pop(root, None)

    @rule(root=root_ids, name=texts)
    def patch_name(self, root, name):
        error = NotFound if root not in self.state else None
        perform(error, lambda: self.db.patch(Warehouse, root, changes={"name": name}))
        if error is None:
            self.state[root]["name"] = name

    @rule(root=root_ids, bin_id=bin_ids, child=child_ids)
    def insert_child(self, root, bin_id, child):
        error = NotFound if root not in self.state else None
        if error is None and child in self.state[root]["bins"][bin_id]:
            error = AlreadyExists
        perform(
            error,
            lambda: self.db.insert_child(
                Warehouse, root, collection=("bins", bin_id, "lots"), record=Lot(id=child)
            ),
        )
        if error is None:
            self.state[root]["bins"][bin_id][child] = plain_lot()

    @rule(root=root_ids, bin_id=bin_ids, child=child_ids)
    def delete_child(self, root, bin_id, child):
        error = NotFound if self.missing(root, bin_id, child) else None
        perform(
            error, lambda: self.db.delete_child(Warehouse, root, at=("bins", bin_id, "lots", child))
        )
        if error is None:
            del self.state[root]["bins"][bin_id][child]

    @rule(root=root_ids, bin_id=bin_ids, child=child_ids, quantity=st.integers(0, 9), note=texts)
    def patch_values(self, root, bin_id, child, quantity, note):
        error = NotFound if self.missing(root, bin_id, child) else None
        changes = {"quantity": Decimal(quantity), "note": note}
        perform(
            error,
            lambda: self.db.patch(
                Warehouse, root, at=("bins", bin_id, "lots", child), changes=changes
            ),
        )
        if error is None:
            self.state[root]["bins"][bin_id][child].update(quantity=quantity, note=note)

    @rule(
        root=root_ids, bin_id=bin_ids, child=child_ids, serial=st.sampled_from([None, "S0", "S1"])
    )
    def patch_unique(self, root, bin_id, child, serial):
        error = NotFound if self.missing(root, bin_id, child) else None
        if error is None and serial is not None:
            for other_root, warehouse in self.state.items():
                for other_bin, lots in warehouse["bins"].items():
                    if any(
                        value["serial"] == serial
                        and (other_root, other_bin, other_child) != (root, bin_id, child)
                        for other_child, value in lots.items()
                    ):
                        error = UniqueViolation
        perform(
            error,
            lambda: self.db.patch(
                Warehouse, root, at=("bins", bin_id, "lots", child), changes={"serial": serial}
            ),
        )
        if error is None:
            self.state[root]["bins"][bin_id][child]["serial"] = serial

    @rule(
        root=root_ids,
        bin_id=bin_ids,
        child=child_ids,
        product_id=st.sampled_from([None, "P0", "P1", "missing"]),
    )
    def patch_reference(self, root, bin_id, child, product_id):
        error = NotFound if self.missing(root, bin_id, child) else None
        if error is None and product_id is not None and product_id not in self.products:
            error = ReferenceViolation
        reference = None if product_id is None else Reference[Product](key=product_id)
        perform(
            error,
            lambda: self.db.patch(
                Warehouse, root, at=("bins", bin_id, "lots", child), changes={"product": reference}
            ),
        )
        if error is None:
            self.state[root]["bins"][bin_id][child]["product"] = product_id

    @rule(product_id=st.sampled_from(["P0", "P1"]))
    def delete_product(self, product_id):
        referenced = any(
            value["product"] == product_id
            for warehouse in self.state.values()
            for lots in warehouse["bins"].values()
            for value in lots.values()
        )
        perform(
            ReferenceViolation if referenced else None, lambda: self.db.delete(Product, product_id)
        )
        if not referenced:
            self.products.discard(product_id)

    @rule(product_id=st.sampled_from(["P0", "P1"]))
    def restore_product(self, product_id):
        self.db.save(Product(id=product_id, name=product_id))
        self.products.add(product_id)

    @rule(root=root_ids, bin_id=bin_ids, child=child_ids)
    def invalid_patch(self, root, bin_id, child):
        before = dump(self.store, ROOTS)
        error = NotFound if self.missing(root, bin_id, child) else ValidationError
        perform(
            error,
            lambda: self.db.patch(
                Warehouse,
                root,
                at=("bins", bin_id, "lots", child),
                changes={"quantity": Decimal(-1)},
            ),
        )
        assert dump(self.store, ROOTS) == before

    @rule(root=root_ids)
    def abort(self, root):
        before = dump(self.store, ROOTS)

        def fail(tx):
            tx.save(Warehouse(id=root, name="aborted"))
            raise RuntimeError("injected abort after aggregate and index replacement")

        with pytest.raises(RuntimeError):
            self.db.atomic(fail)
        assert dump(self.store, ROOTS) == before

    @invariant()
    def logical_and_physical_state_agree(self):
        observed = {}
        for value in self.db.scan(Warehouse, limit=10):
            observed[value.id] = {
                "name": value.name,
                "bins": {
                    bin_id: {
                        child: {
                            "quantity": int(lot.quantity),
                            "note": lot.note,
                            "serial": lot.serial,
                            "product": None if lot.product is None else lot.product.key,
                        }
                        for child, lot in bin_.lots.items()
                    }
                    for bin_id, bin_ in value.bins.items()
                },
            }
        assert observed == self.state
        assert {value.id for value in self.db.scan(Product, limit=10)} == self.products
        audit(self.store, self.state, self.products)


def test_generated_mutation_sequences(store):
    from mccann_hub.yhmf import NativeStore

    run_state_machine_as_test(
        lambda: MutationMachine(store), settings=sequence_settings(isinstance(store, NativeStore))
    )
