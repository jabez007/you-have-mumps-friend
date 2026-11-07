# SPDX-License-Identifier: GPL-3.0-or-later
import json
from decimal import Decimal
from pathlib import Path

from examples.inventory import MODELS, Product
from mccann_hub.yhmf import Database, Field, Registry, SchemaSnapshot
from mccann_hub.yhmf.schemas import canonical_json
from tests.test_schema_contracts import seed_legacy

FIXTURE = Path(__file__).with_name("fixtures") / "inventory-before-field-reuse.json"


def test_shared_field_reuse_preserves_every_existing_inventory_contract():
    fixture = json.loads(FIXTURE.read_text())
    registry = Registry(*MODELS)
    for model in MODELS:
        assert registry.spec(model).snapshot == canonical_json(fixture["schemas"][model.__name__])


def test_inherited_keys_keep_legacy_metadata_and_unique_index_behavior(store):
    fixture = json.loads(FIXTURE.read_text())
    legacy = SchemaSnapshot(canonical_json(fixture["legacy_product"]))
    seed_legacy(store, Product(id="P", sku="SKU", name="Product"), legacy)
    db = Database(store, Registry(*MODELS))
    assert db.inspect_schema(Product).installed == legacy
    assert db.get(Product, "P").sku == "SKU"
    assert [row.id for row in db.query(Product).where(sku="SKU").all()] == ["P"]
    db.patch(Product, "P", changes={"name": "Updated"})
    assert db.get(Product, "P").name == "Updated"
    assert db.inspect_schema(Product).installed == legacy
    assert db.check_indexes(Product).issues == ()


def test_extending_an_inherited_model_uses_the_existing_additive_executor(store):
    class Priced(Product):
        __schema_version__ = 2
        price: Decimal = Field(default=Decimal("1.25"), index=True)

    Database(store, Registry(Product)).insert(Product(id="P", sku="SKU", name="Product"))
    db = Database(store, Registry(Priced))
    status = db.begin_migration(Product, Priced, plan=db.plan_migration(Priced))
    for _ in range(100):
        if status.phase == "complete":
            break
        status = db.advance_migration(status.migration_id, Product, Priced, batch_size=1)
    assert status.phase == "complete"
    assert db.get(Priced, "P").price == Decimal("1.25")
    assert [row.id for row in db.query(Priced).where(price=Decimal("1.25")).all()] == ["P"]
    assert db.check_indexes(Priced).issues == ()
