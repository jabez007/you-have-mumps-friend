# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit inventory transactions, built only from public YHMF operations."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import cast

from pydantic import TypeAdapter, ValidationError

from mccann_hub.yhmf import Database, Patch, Reference
from mccann_hub.yhmf.errors import NotFound

from .models import (
    Balance,
    Location,
    Lot,
    Movement,
    MovementKind,
    Position,
    Product,
    Quantity,
    Warehouse,
)

_QUANTITY = TypeAdapter(Quantity)


class InventoryError(ValueError):
    """The requested operation violates an inventory business rule."""


class InsufficientStock(InventoryError):
    pass


@dataclass(frozen=True)
class BinAddress:
    warehouse: str
    location: str
    bin: str

    @property
    def path(self) -> tuple[str, ...]:
        return ("locations", self.location, "bins", self.bin)

    def balance_path(self, lot: str) -> tuple[str, ...]:
        return (*self.path, "balances", lot)

    def position(self) -> Position:
        return Position(
            warehouse=Reference[Warehouse](key=self.warehouse), location=self.location, bin=self.bin
        )


@dataclass(frozen=True)
class StockRow:
    address: BinAddress
    lot: str
    product: str
    quantity: Decimal


@dataclass(frozen=True)
class StockPage:
    rows: tuple[StockRow, ...]
    next_warehouse: str | None


@dataclass(frozen=True)
class BalanceChange:
    address: BinAddress
    value: Balance
    existed: bool


def _quantity(value: Decimal, *, positive: bool) -> Decimal:
    try:
        result = _QUANTITY.validate_python(value, strict=True)
    except ValidationError:
        raise InventoryError(
            "Quantity requires a finite Decimal with at most 12 whole and six fractional digits"
        ) from None
    if not result.is_finite() or result == 0 or (positive and result < 0):
        raise InventoryError(
            "Quantity must be positive" if positive else "Adjustment must be nonzero"
        )
    return result


def _time(value: str | datetime | None) -> datetime:
    try:
        instant = (
            datetime.now(UTC)
            if value is None
            else (value if isinstance(value, datetime) else datetime.fromisoformat(value))
        )
        offset = instant.utcoffset()
    except (ValueError, TypeError, OverflowError):
        raise InventoryError(
            "Movement timestamp must be an ISO 8601 time with a timezone"
        ) from None
    if offset is None:
        raise InventoryError("Movement timestamps require a timezone")
    try:
        return instant.astimezone(UTC)
    except (OverflowError, ValueError, TypeError):
        raise InventoryError("Movement timestamp is outside the UTC calendar") from None


def _change(tx: Database, address: BinAddress, lot: Lot, delta: Decimal) -> BalanceChange:
    try:
        path = tx.path(
            Warehouse,
            lambda w: w.locations[address.location].bins[address.bin].balances[lot.id],
        )
        current = tx.get(Warehouse, address.warehouse, at=path)
    except NotFound:
        current = None
    if current is not None and (
        current.lot.key != lot.id or current.product.key != lot.product.key
    ):
        raise InventoryError("Stored balance does not match its lot and product")
    with localcontext() as context:
        context.prec = 40  # Exact arithmetic within the declared 18-digit quantity domain.
        quantity = (Decimal(0) if current is None else current.quantity) + delta
    if quantity < 0:
        raise InsufficientStock("Source balance cannot become negative")
    value = Balance(
        id=lot.id, lot=Reference[Lot](key=lot.id), product=lot.product, quantity=quantity
    )
    return BalanceChange(address, value, current is not None)


class InventoryTransactions:
    _change = staticmethod(_change)

    def __init__(self, db: Database) -> None:
        self.db = db

    def _apply_balances(self, tx: Database, changes: tuple[BalanceChange, ...]) -> None:
        patches = []
        for change in changes:
            address, value = change.address, change.value
            if change.existed:
                patches.append(
                    Patch(
                        Warehouse,
                        address.warehouse,
                        at=address.balance_path(value.id),
                        changes={"quantity": value.quantity},
                    )
                )
            else:
                tx.insert_child(
                    Warehouse,
                    address.warehouse,
                    collection=(*address.path, "balances"),
                    record=value,
                )
        if patches:
            tx.patch_many(patches)

    def receive(
        self,
        movement_id: str,
        lot_id: str,
        destination: BinAddress,
        quantity: Decimal,
        *,
        reason: str = "stock receipt",
        occurred_at: str | datetime | None = None,
    ) -> Movement:
        amount, timestamp = _quantity(quantity, positive=True), _time(occurred_at)

        def apply(tx: Database) -> Movement:
            lot = tx.get(Lot, lot_id)
            self._apply_balances(tx, (self._change(tx, destination, lot, amount),))
            return self._record(
                tx, movement_id, lot, amount, "receive", destination, reason, timestamp
            )

        return self.db.atomic(apply)

    def move(
        self,
        movement_id: str,
        lot_id: str,
        source: BinAddress,
        destination: BinAddress,
        quantity: Decimal,
        *,
        reason: str = "stock transfer",
        occurred_at: str | datetime | None = None,
    ) -> Movement:
        if source == destination:
            raise InventoryError("Source and destination bins must differ")
        amount, timestamp = _quantity(quantity, positive=True), _time(occurred_at)

        def apply(tx: Database) -> Movement:
            lot = tx.get(Lot, lot_id)
            changes = (
                self._change(tx, source, lot, amount.copy_negate()),
                self._change(tx, destination, lot, amount),
            )
            self._apply_balances(tx, changes)
            return self._record(
                tx, movement_id, lot, amount, "move", destination, reason, timestamp, source
            )

        return self.db.atomic(apply)

    def adjust(
        self,
        movement_id: str,
        lot_id: str,
        destination: BinAddress,
        delta: Decimal,
        *,
        reason: str,
        occurred_at: str | datetime | None = None,
    ) -> Movement:
        if not reason.strip():
            raise InventoryError("An inventory adjustment requires a reason")
        amount, timestamp = _quantity(delta, positive=False), _time(occurred_at)

        def apply(tx: Database) -> Movement:
            lot = tx.get(Lot, lot_id)
            self._apply_balances(tx, (self._change(tx, destination, lot, amount),))
            return self._record(
                tx, movement_id, lot, amount, "adjust", destination, reason, timestamp
            )

        return self.db.atomic(apply)

    @staticmethod
    def _record(
        tx: Database,
        movement_id: str,
        lot: Lot,
        quantity: Decimal,
        kind: MovementKind,
        destination: BinAddress,
        reason: str,
        timestamp: datetime,
        source: BinAddress | None = None,
    ) -> Movement:
        value = Movement(
            id=movement_id,
            kind=kind,
            product=lot.product,
            lot=Reference[Lot](key=lot.id),
            quantity=quantity,
            source=None if source is None else source.position(),
            destination=destination.position(),
            reason=reason,
            occurred_at=timestamp,
        )
        tx.insert(value)
        return value

    def history(self, lot: str, *, limit: int = 100, after: str | None = None) -> list[Movement]:
        return (
            self.db.query(Movement).where(lot=Reference[Lot](key=lot)).all(limit=limit, after=after)
        )


class Inventory(InventoryTransactions):
    def stock_at(self, address: BinAddress) -> tuple[StockRow, ...]:
        path = self.db.path(Warehouse, lambda w: w.locations[address.location].bins[address.bin])
        bin_ = self.db.get(Warehouse, address.warehouse, at=path)
        return tuple(_row(address, value) for value in bin_.balances.values())

    def stock_in_warehouse(
        self, warehouse: str, *, location: str | None = None
    ) -> tuple[StockRow, ...]:
        locations: Iterable[Location]
        if location is None:
            locations = self.db.get(Warehouse, warehouse).locations.values()
        else:
            path = self.db.path(Warehouse, lambda w: w.locations[location])
            locations = (self.db.get(Warehouse, warehouse, at=path),)
        return tuple(_rows(warehouse, locations))

    def stock_by_product(
        self, product: str, *, warehouse_limit: int = 20, after: str | None = None
    ) -> StockPage:
        query = self.db.query(Warehouse).where(
            **{"locations.bins.balances.product": Reference[Product](key=product)}
        )
        warehouses = query.all(limit=warehouse_limit, after=after)
        rows = tuple(
            row
            for warehouse in warehouses
            for row in _rows(warehouse.id, warehouse.locations.values())
            if row.product == product
        )
        cursor = warehouses[-1].id if len(warehouses) == warehouse_limit else None
        return StockPage(rows, cursor)


def _rows(warehouse: str, locations: Iterable[Location]) -> Iterator[StockRow]:
    for location in locations:
        for bin_ in location.bins.values():
            address = BinAddress(warehouse, location.id, bin_.id)
            for value in bin_.balances.values():
                yield _row(address, value)


def _row(address: BinAddress, value: Balance) -> StockRow:
    # All independent model keys in this application are declared strings.
    return StockRow(address, cast(str, value.lot.key), cast(str, value.product.key), value.quantity)
