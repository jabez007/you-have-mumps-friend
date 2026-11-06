# SPDX-License-Identifier: GPL-3.0-or-later
"""Root and nested index-retirement declarations shared with native workers."""

import runpy
from decimal import Decimal
from pathlib import Path

from mccann_hub.yhmf import ChildField, Children, Field, Model


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    name: str = Field(index=True)
    price: Decimal = Field(unique=True)
    locked: str = Field(unique=True)


class RelaxedProduct(Product):
    __schema_version__ = 2
    sku: str | None = Field(default=None, index=True)
    name: str
    price: Decimal


class UnindexedProduct(RelaxedProduct):
    __schema_version__ = 3
    sku: str | None = None


_nested = runpy.run_path(str(Path(__file__).with_name("nested_migration_models.py")))
RelatedTarget, Warehouse = _nested["Target"], _nested["WarehouseV2"]
SourceSample, SourceLot = _nested["SampleV2"], _nested["LotV2"]


class RelaxedSample(SourceSample):
    __schema_version__ = 3
    reading: Decimal = Field(index=True)
    quality: int = 7


class RelaxedLot(SourceLot):
    __schema_version__ = 3
    serial: str | None = Field(default=None, index=True)
    amount: Decimal
    note: str | None = None
    samples: Children[RelaxedSample] = ChildField(key="key")


class RelaxedWarehouse(Warehouse):
    __schema_version__ = 3
    locked: str = Field(index=True)
    note: int = 9
    lots: Children[RelaxedLot] = ChildField(key="code")


def nested_records():
    return [Warehouse.model_validate(record.model_dump()) for record in _nested["sample_records"]()]
