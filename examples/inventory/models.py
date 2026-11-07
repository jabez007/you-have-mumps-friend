# SPDX-License-Identifier: GPL-3.0-or-later
"""Warehouse ownership and independent catalog, lot, and ledger identities."""

from decimal import Decimal
from typing import Annotated, Self

from pydantic import ConfigDict, model_validator

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference

Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=6)]


class Identified(Model):
    """Shared declaration only; concrete roots and owned models choose their own storage."""

    id: str = Field(primary_key=True, min_length=1)


class Product(Identified):
    __global__ = "^InventoryProduct"
    sku: str = Field(unique=True, min_length=1)
    name: str = Field(min_length=1)


class Lot(Identified):
    __global__ = "^InventoryLot"
    product: Reference[Product] = Field(index=True)


class SupplierProduct(Identified):
    """An owned membership link; the independently stored Product keeps its lifecycle."""

    product: Reference[Product]

    @model_validator(mode="after")
    def product_identity(self) -> Self:
        if self.id != self.product.key:
            raise ValueError("Supplier membership key must match its product reference")
        return self


class Supplier(Identified):
    __global__ = "^InventorySupplier"
    name: str = Field(min_length=1)
    products: Children[SupplierProduct] = ChildField(key="id")


class Balance(Identified):
    lot: Reference[Lot] = Field(index=True)
    product: Reference[Product] = Field(index=True)
    quantity: Quantity = Field(default=Decimal(0), ge=0)


class Bin(Identified):
    balances: Children[Balance] = ChildField(key="id")


class Location(Identified):
    bins: Children[Bin] = ChildField(key="id")


class Warehouse(Identified):
    __global__ = "^InventoryWarehouse"
    name: str
    locations: Children[Location] = ChildField(key="id")


class Position(Model):
    warehouse: Reference[Warehouse] = Field(index=True)
    location: str = Field(min_length=1)
    bin: str = Field(min_length=1)


class Movement(Identified):
    __global__ = "^InventoryMovement"
    model_config = ConfigDict(frozen=True)
    kind: str = Field(pattern=r"^(receive|move|adjust)$")
    product: Reference[Product] = Field(index=True)
    lot: Reference[Lot] = Field(index=True)
    quantity: Quantity
    source: Position | None = None
    destination: Position
    reason: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)


MODELS = (Product, Lot, Warehouse, Movement, Supplier)
