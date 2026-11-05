# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable migration worker; rendezvous IO is test instrumentation only."""

import json
import runpy
import sys
from decimal import Decimal
from pathlib import Path as FilePath

from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.errors import MaintenanceActive, SchemaMismatch
from mccann_hub.yhmf.maintenance import GATE, job_path
from mccann_hub.yhmf.schemas import schema_path

_models = runpy.run_path(str(FilePath(__file__).with_name("migration_models.py")))
Product, ProductV2 = _models["Product"], _models["ProductV2"]


class RendezvousStore(NativeStore):
    def __init__(self, operation, migration_id):
        super().__init__()
        self.operation = operation
        self.waiting = operation != "resume"
        self.reads = 0
        self.guard = job_path(migration_id) if operation == "advance" else GATE
        if operation == "legacy_write":
            self.guard = schema_path(Product.__global__)

    def rendezvous(self):
        self.waiting = False
        print("ready", flush=True)
        if sys.stdin.readline().strip() != "go":
            raise RuntimeError("Controller did not release the worker")

    def get(self, path):
        value = super().get(path)
        if path == self.guard:
            self.reads += 1
            if self.waiting and self.operation != "crash":
                self.rendezvous()
        return value

    def set(self, path, value):
        super().set(path, value)
        if self.waiting and self.operation == "crash" and path.subscripts[-1] == b"note":
            self.rendezvous()


def run(operation, migration_id):
    store = RendezvousStore(operation, migration_id)
    db = Database(store, Registry(ProductV2))
    try:
        if operation == "write":
            Database(store, Registry(Product)).save(
                Product(id="P0", name="Changed", sku="SKU0", price=Decimal("1.00"))
            )
        elif operation == "legacy_write":
            spec = Registry(Product).spec(Product)

            def legacy():
                # Reproduce a pre-gate client's transactional fingerprint check.
                if store.get(schema_path(spec.global_name)) != spec.fingerprint:
                    raise SchemaMismatch("Legacy client observed the schema barrier")
                store.set(spec.path("P0").child(b"name"), b"s:Changed")

            store.atomic(legacy)
        elif operation == "begin":
            plan = db.plan_migration(ProductV2)
            status = db.begin_migration(Product, ProductV2, plan=plan)
            print(json.dumps(status.to_dict()), flush=True)
            return
        else:
            status = db.advance_migration(migration_id, Product, ProductV2, batch_size=1)
            print(json.dumps({**status.to_dict(), "reads": store.reads}), flush=True)
            return
        result = "committed"
    except (MaintenanceActive, SchemaMismatch):
        result = "restricted"
    print(json.dumps({"result": result, "reads": store.reads}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
