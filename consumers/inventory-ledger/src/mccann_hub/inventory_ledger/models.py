# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent catalog/locations, position ownership and immutable ledger snapshots."""

from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference


class Product(Model):
    __global__ = "^ConsumerProduct"
    id: str = Field(primary_key=True)
    sku: str = Field(unique=True)
    name: str


class Warehouse(Model):
    __global__ = "^ConsumerWarehouse"
    id: str = Field(primary_key=True)
    name: str


class Lot(Model):
    __global__ = "^ConsumerLot"
    id: str = Field(primary_key=True)
    product: Reference[Product] = Field(index=True)


class Balance(Model):
    id: str = Field(primary_key=True)
    lot: Reference[Lot] = Field(index=True)
    product: Reference[Product] = Field(index=True)
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class LocationV1(Model):
    __global__ = "^ConsumerLocation"
    id: str = Field(primary_key=True)
    warehouse: Reference[Warehouse] = Field(index=True)
    balances: Children[Balance] = ChildField(key="id")


class Location(LocationV1):
    __schema_version__ = 2
    description: str = Field(default="", index=True)


class Movement(Model):
    __global__ = "^ConsumerMovement"
    id: str = Field(primary_key=True)
    kind: str
    lot: Reference[Lot] = Field(index=True)
    product: Reference[Product] = Field(index=True)
    source: str
    destination: str = Field(index=True)
    quantity: Decimal
    reason: str
    occurred_at: str


def models(location: type[LocationV1] = Location) -> tuple[type[Model], ...]:
    return (Product, Warehouse, Lot, location, Movement)
