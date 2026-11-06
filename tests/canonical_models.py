# SPDX-License-Identifier: GPL-3.0-or-later
"""Stable identities include globals and field names, never Python class names."""

from decimal import Decimal
from typing import Annotated

from pydantic import StringConstraints

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    sku: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(unique=True)


class Details(Model):
    note: str | None = None
    active: bool = True
    payload: bytes = b"\x00\xff"
    ratio: float = Field(default=0.5, ge=0, le=1)


class Lot(Model):
    id: str = Field(primary_key=True)
    product: Reference[Product] = Field(index=True)
    quantity: Decimal = Field(
        default=Decimal("0.00"), ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    details: Details | None = None


class Bin(Model):
    number: int = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="id")


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    __schema_version__ = 3
    id: str = Field(primary_key=True)
    name: str = Field(index=True, default="Main")
    bins: Children[Bin] = ChildField(key="number")
