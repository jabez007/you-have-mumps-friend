# SPDX-License-Identifier: GPL-3.0-or-later
"""Warehouse ownership and independent catalog, lot, and ledger identities."""

from decimal import Decimal
from typing import Annotated, Self

from pydantic import ConfigDict, model_validator

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference

Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]


class Product(Model):
    __global__ = "^InventoryProduct"
    id: str = Field(primary_key=True, min_length=1)
    sku: str = Field(unique=True, min_length=1)
    name: str = Field(min_length=1)


class Lot(Model):
    __global__ = "^InventoryLot"
    id: str = Field(primary_key=True, min_length=1)
    product: Reference[Product] = Field(index=True)


class SupplierProduct(Model):
    """An owned membership link; the independently stored Product keeps its lifecycle."""

    id: str = Field(primary_key=True, min_length=1)
    product: Reference[Product]

    @model_validator(mode="after")
    def product_identity(self) -> Self:
        if self.id != self.product.key:
            raise ValueError("Supplier membership key must match its product reference")
        return self


class Supplier(Model):
    __global__ = "^InventorySupplier"
    id: str = Field(primary_key=True, min_length=1)
    name: str = Field(min_length=1)
    products: Children[SupplierProduct] = ChildField(key="id")


class Balance(Model):
    id: str = Field(primary_key=True, min_length=1)
    lot: Reference[Lot] = Field(index=True)
    product: Reference[Product] = Field(index=True)
    quantity: Quantity = Field(default=Decimal(0), ge=0)


class Bin(Model):
    id: str = Field(primary_key=True, min_length=1)
    balances: Children[Balance] = ChildField(key="id")


class Location(Model):
    id: str = Field(primary_key=True, min_length=1)
    bins: Children[Bin] = ChildField(key="id")


class Warehouse(Model):
    __global__ = "^InventoryWarehouse"
    id: str = Field(primary_key=True, min_length=1)
    name: str
    locations: Children[Location] = ChildField(key="id")


class Position(Model):
    warehouse: Reference[Warehouse] = Field(index=True)
    location: str = Field(min_length=1)
    bin: str = Field(min_length=1)


class Movement(Model):
    __global__ = "^InventoryMovement"
    model_config = ConfigDict(frozen=True)
    id: str = Field(primary_key=True, min_length=1)
    kind: str = Field(pattern=r"^(receive|move|adjust)$")
    product: Reference[Product] = Field(index=True)
    lot: Reference[Lot] = Field(index=True)
    quantity: Quantity
    source: Position | None = None
    destination: Position
    reason: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)


MODELS = (Product, Lot, Warehouse, Movement, Supplier)
