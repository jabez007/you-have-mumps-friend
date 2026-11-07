# SPDX-License-Identifier: GPL-3.0-or-later
"""Compiled by strict mypy in CI; selectors retain their ordinary model types."""

from typing import assert_type

from mccann_hub.yhmf import Database, ModelPath, Reference, ReferenceFactory

from .models import Balance, Movement, Position, Product, Warehouse


def checked_example(db: Database) -> None:
    path = db.path(Warehouse, lambda w: w.locations["DOCK"].bins["B1"].balances["LOT"])
    assert_type(path, ModelPath[Balance])
    assert_type(db.get(Warehouse, "MAIN", at=path), Balance)
    source = db.path(Movement, lambda m: m.source)
    assert_type(source, ModelPath[Position])
    assert_type(db.get(Movement, "M", at=source), Position)
    product = db.registry.reference(Product, lambda p: p.id)
    assert_type(product, ReferenceFactory[Product, str])
    assert_type(product("P"), Reference[Product])
