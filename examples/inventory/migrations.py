# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen v1 ledger declarations and its explicit UTC timestamp conversion.

Keep these historical fields independent of future domain changes. The committed
pre-refactor inventory schema fixture verifies their persistence contract.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import ConfigDict

from mccann_hub.yhmf import Field, Model, Reference, Transform

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


def _timestamp(record: LegacyMovement) -> Movement:
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
    return Movement.model_validate(
        record.model_dump(exclude={"occurred_at"}) | {"occurred_at": instant}
    )


MOVEMENT_TIMESTAMPS = Transform("inventory.movement.utc.v2", _timestamp)
