# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen inventory declarations and explicit timestamp/stored-total conversions.

Keep these historical fields independent of future domain changes. The committed
pre-refactor inventory schema fixture verifies their persistence contract.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import ConfigDict

from mccann_hub.yhmf import ChildField, Children, Field, Model, Reference, Transform

from .models import Lot, Movement, Product, Warehouse


class LegacyPosition(Model):
    warehouse: Reference[Warehouse] = Field(index=True)
    location: str = Field(min_length=1)
    bin: str = Field(min_length=1)


class LegacyMovement(Model):
    __global__ = "^InventoryMovement"
    __schema_version__ = 1
    model_config = ConfigDict(frozen=True)
    id: str = Field(primary_key=True, min_length=1)
    kind: str = Field(pattern=r"^(receive|move|adjust)$")
    product: Reference[Product] = Field(index=True)
    lot: Reference[Lot] = Field(index=True)
    quantity: Annotated[Decimal, Field(max_digits=18, decimal_places=6)]
    source: LegacyPosition | None = None
    destination: LegacyPosition
    reason: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)


class MovementV2(Model):
    """Frozen UTC datetime contract before the ordered timestamp index."""

    __global__ = "^InventoryMovement"
    __schema_version__ = 2
    model_config = ConfigDict(frozen=True)
    id: str = Field(primary_key=True, min_length=1)
    kind: str = Field(pattern=r"^(receive|move|adjust)$")
    product: Reference[Product] = Field(index=True)
    lot: Reference[Lot] = Field(index=True)
    quantity: Annotated[Decimal, Field(max_digits=18, decimal_places=6)]
    source: LegacyPosition | None = None
    destination: LegacyPosition
    reason: str = Field(min_length=1)
    occurred_at: datetime


class MovementV3(Model):
    """Frozen ordered timestamp/string-kind contract before finite choices."""

    __global__ = "^InventoryMovement"
    __schema_version__ = 3
    model_config = ConfigDict(frozen=True)
    id: str = Field(primary_key=True, min_length=1)
    kind: str = Field(pattern=r"^(receive|move|adjust)$")
    product: Reference[Product] = Field(index=True)
    lot: Reference[Lot] = Field(index=True)
    quantity: Annotated[Decimal, Field(max_digits=18, decimal_places=6)]
    source: LegacyPosition | None = None
    destination: LegacyPosition
    reason: str = Field(min_length=1)
    occurred_at: datetime = Field(ordered=True)


def _timestamp(record: LegacyMovement) -> MovementV2:
    # Conversion deliberately accepts only historical ISO strings, independently
    # of future service input conveniences or the wall clock.
    try:
        instant = datetime.fromisoformat(record.occurred_at)
        if instant.utcoffset() is None:
            raise ValueError("Missing timezone")
        instant = instant.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError(
            "Legacy movement timestamp requires a representable aware instant"
        ) from None
    return MovementV2.model_validate(
        record.model_dump(exclude={"occurred_at"}) | {"occurred_at": instant}
    )


MOVEMENT_TIMESTAMPS = Transform("inventory.movement.utc.v2", _timestamp)


def _ordered_timestamp(record: LegacyMovement) -> Movement:
    return Movement.model_validate(_timestamp(record).model_dump())


MOVEMENT_TIMESTAMPS_V3 = Transform("inventory.movement.utc-ordered.v3", _ordered_timestamp)


class LegacyWarehouseIdentity(Model):
    __schema_version__ = 1
    id: str = Field(primary_key=True, min_length=1)


class LegacyBalance(LegacyWarehouseIdentity):
    lot: Reference[Lot] = Field(index=True)
    product: Reference[Product] = Field(index=True)
    quantity: Annotated[Decimal, Field(max_digits=18, decimal_places=6)] = Field(
        default=Decimal(0), ge=0
    )


class LegacyBin(LegacyWarehouseIdentity):
    balances: Children[LegacyBalance] = ChildField(key="id")


class LegacyLocation(LegacyWarehouseIdentity):
    bins: Children[LegacyBin] = ChildField(key="id")


class LegacyWarehouse(LegacyWarehouseIdentity):
    __global__ = "^InventoryWarehouse"
    name: str
    locations: Children[LegacyLocation] = ChildField(key="id")


class WarehouseV2(LegacyWarehouseIdentity):
    """Frozen root-total contract, before owned location totals."""

    __global__ = "^InventoryWarehouse"
    __schema_version__ = 2
    name: str
    locations: Children[LegacyLocation] = ChildField(key="id")
    stored_on_hand: Decimal = Field(sum_of="locations.bins.balances.quantity", index=True, ge=0)


def _warehouse_total(record: LegacyWarehouse) -> WarehouseV2:
    # The framework derives and pins the target stored sum after normal validation.
    return WarehouseV2.model_validate(record.model_dump())


WAREHOUSE_TOTALS = Transform("inventory.warehouse.total.v2", _warehouse_total)


def _location_totals(record: LegacyWarehouse | WarehouseV2) -> Warehouse:
    return Warehouse.model_validate(record.model_dump())


WAREHOUSE_TOTALS_V3 = Transform("inventory.warehouse.location-totals.v3", _location_totals)
