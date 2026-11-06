# SPDX-License-Identifier: GPL-3.0-or-later
from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str


class Lot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0, index=True)
    note: str = ""
    serial: str | None = Field(default=None, unique=True)
    product: Reference[Product] | None = Field(default=None, index=True)


class Bin(Model):
    id: str = Field(primary_key=True)
    title: str = ""
    lots: Children[Lot] = ChildField(key="id")


class Address(Model):
    city: str


class Warehouse(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    name: str = Field(default="Main", index=True)
    address: Address | None = None
    bins: Children[Bin] = ChildField(key="id")


def warehouse(size=1):
    return Warehouse(
        id="W",
        address=Address(city="Chicago"),
        bins={"A": Bin(id="A", lots={str(i): Lot(id=str(i)) for i in range(size)})},
    )
