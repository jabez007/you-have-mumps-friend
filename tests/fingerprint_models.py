# SPDX-License-Identifier: GPL-3.0-or-later
"""Identical declaration executed under each qualified Pydantic version."""

from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Field, Model


class Lot(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0, index=True)


class Stock(Model):
    __global__ = "^YHMFTestWarehouse"
    id: str = Field(primary_key=True)
    price: Decimal
    lots: Children[Lot] = ChildField(key="id")
