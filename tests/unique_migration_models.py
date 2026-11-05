# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared declarations for unique migration tests and native workers."""

from decimal import Decimal

from mccann_hub.yhmf import Field, Model


class Product(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, index=True)
    serial: str = Field(unique=True)
    price: Decimal


class UniqueProduct(Product):
    __schema_version__ = 2
    sku: str | None = Field(default=None, unique=True)
    price: Decimal = Field(unique=True)
