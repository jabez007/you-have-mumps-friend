# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent location/bin lifetimes; stock stays hierarchical within one bin."""

from decimal import Decimal
from typing import Literal

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference

from .models import MODELS, Balance, Identified, Warehouse


def coordinate_key(*parts: str) -> str:
    """Injective, inspectable coordinates; normal encoded-key size limits still apply."""
    return "".join(f"{len(part.encode('utf-8'))}:{part}" for part in parts)


class StockLocation(Identified):
    __global__ = "^InventoryLocation"
    warehouse: Reference[Warehouse] = Field(index=True)
    code: str = Field(min_length=1)


class StockBin(Identified):
    __global__ = "^InventoryBin"
    warehouse: Reference[Warehouse] = Field(index=True)
    location: Reference[StockLocation] = Field(index=True)
    code: str = Field(min_length=1)
    balances: Children[Balance] = ChildField(key="id")
    stored_on_hand: Decimal = Field(sum_of="balances.quantity", index=True, ge=0)


class StockMoveState(Model):
    __global__ = "^InventoryStockMove"
    id: str = Field(primary_key=True)
    status: Literal["copying", "complete"]
    after: str | None = None


BIN_MODELS = (*MODELS, StockLocation, StockBin, StockMoveState)
