# SPDX-License-Identifier: GPL-3.0-or-later
"""Small explicit business transactions, with no framework-private imports."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import cast

from mccann_hub.yhmf import Database, Reference
from mccann_hub.yhmf.errors import NotFound

from .models import Balance, Location, LocationV1, Lot, Movement, Product, Warehouse


class StockError(ValueError):
    pass


class Ledger:
    def __init__(self, db: Database, location: type[LocationV1] = Location) -> None:
        self.db, self.location = db, location

    def create(self) -> None:
        def seed(tx: Database) -> None:
            tx.insert(Product(id="P", sku="SKU", name="Widget"))
            tx.insert(Warehouse(id="W", name="Main"))
            tx.insert(Lot(id="LOT", product=Reference[Product](key="P")))
            for key in ("DOCK", "STORE"):
                tx.insert(self.location(id=key, warehouse=Reference[Warehouse](key="W")))

        self.db.atomic(seed)

    def _change(self, tx: Database, location: str, lot: Lot, delta: Decimal) -> None:
        at = ("balances", lot.id)
        try:
            current = cast(Balance, tx.get(self.location, location, at=at))
        except NotFound:
            current = None
        if current and (current.product.key != lot.product.key or current.lot.key != lot.id):
            raise StockError("Stored balance does not match its lot/product")
        with localcontext() as context:
            context.prec = 40
            quantity = (Decimal(0) if current is None else current.quantity) + delta
        if quantity < 0:
            raise StockError("Insufficient stock")
        if current is None:
            tx.insert_child(
                self.location,
                location,
                collection=("balances",),
                record=Balance(
                    id=lot.id,
                    lot=Reference[Lot](key=lot.id),
                    product=lot.product,
                    quantity=quantity,
                ),
            )
        else:
            tx.patch(self.location, location, at=at, changes={"quantity": quantity})

    def _record(
        self,
        key: str,
        lot_id: str,
        destination: str,
        amount: Decimal,
        reason: str,
        kind: str,
        source: str = "",
    ) -> Movement:
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount == 0:
            raise StockError("Use a nonzero finite Decimal quantity")
        if kind != "adjust" and amount < 0:
            raise StockError("Receipt/transfer quantities must be positive")
        if not reason.strip():
            raise StockError("A reason is required")
        timestamp = datetime.now(UTC).isoformat()  # Stable across transaction retries.

        def apply(tx: Database) -> Movement:
            lot = tx.get(Lot, lot_id)
            if source:
                self._change(tx, source, lot, amount.copy_negate())
            self._change(tx, destination, lot, amount)
            value = Movement(
                id=key,
                kind=kind,
                lot=Reference[Lot](key=lot_id),
                product=lot.product,
                source=source,
                destination=destination,
                quantity=amount,
                reason=reason,
                occurred_at=timestamp,
            )
            tx.insert(value)
            return value

        return self.db.atomic(apply)

    def receive(
        self, key: str, lot: str, destination: str, quantity: Decimal, reason: str = "Receipt"
    ) -> Movement:
        return self._record(key, lot, destination, quantity, reason, "receive")

    def transfer(
        self, key: str, lot: str, source: str, destination: str, quantity: Decimal
    ) -> Movement:
        if source == destination:
            raise StockError("Choose different source and destination locations")
        return self._record(key, lot, destination, quantity, "Transfer", "transfer", source)

    def adjust(self, key: str, lot: str, location: str, delta: Decimal, *, reason: str) -> Movement:
        return self._record(key, lot, location, delta, reason, "adjust")

    def stock(
        self,
        *,
        product: str | None = None,
        location: str | None = None,
        lot: str | None = None,
        limit: int = 20,
        after: str | None = None,
    ) -> list[LocationV1]:
        if sum(value is not None for value in (product, location, lot)) != 1:
            raise StockError("Choose exactly one product, location or lot filter")
        if location is not None:
            return [self.db.get(self.location, location)]
        field, value = (
            ("balances.lot", Reference[Lot](key=lot))
            if lot is not None
            else (
                "balances.product",
                Reference[Product](key=product if product is not None else ""),
            )
        )
        return self.db.query(self.location).where(**{field: value}).all(limit=limit, after=after)

    def history(self, lot: str, *, limit: int = 100, after: str | None = None) -> list[Movement]:
        return (
            self.db.query(Movement).where(lot=Reference[Lot](key=lot)).all(limit=limit, after=after)
        )
