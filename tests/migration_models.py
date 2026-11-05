# SPDX-License-Identifier: GPL-3.0-or-later
"""Identical declarations for migration tests and disposable native workers."""

from decimal import Decimal

from mccann_hub.yhmf import Field, Model


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str
    sku: str = Field(unique=True)
    price: Decimal


class ProductV2(Product):
    __schema_version__ = 2
    note: str | None = Field(default=None, index=True)
    quantity: int = 7
    name: str = Field(index=True)
