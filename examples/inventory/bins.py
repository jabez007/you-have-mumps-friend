# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory with independently rooted bins. Run python -m examples.inventory.bins."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import cast

from mccann_hub.yhmf import Database, MemoryStore, Patch, Reference, Registry
from mccann_hub.yhmf.errors import NotFound

from .bin_models import BIN_MODELS, StockBin, StockLocation, StockMoveState, coordinate_key
from .ledger import (
    BalanceChange,
    BinAddress,
    InsufficientStock,
    InventoryError,
    InventoryTransactions,
    StockRow,
    _row,
)
from .models import Balance, Lot, Product, Warehouse


@dataclass(frozen=True)
class BinStockPage:
    rows: tuple[StockRow, ...]
    next_bin: str | None


def require_ready(db: Database) -> None:
    try:
        state = db.get(StockMoveState, "bins-v1")
    except NotFound:
        return
    if state.status != "complete":
        raise InventoryError("Stock layout move is in progress; resume it before writing")


def create_bin(db: Database, address: BinAddress) -> StockBin:
    """Explicit setup; callers create Warehouse first. Existing coordinates are immutable."""

    def install(tx: Database) -> StockBin:
        require_ready(tx)
        warehouse = tx.get(Warehouse, address.warehouse)
        if warehouse.locations:
            raise InventoryError("Move legacy warehouse stock before creating independent bins")
        location_id = coordinate_key(address.warehouse, address.location)
        try:
            location = tx.get(StockLocation, location_id)
        except NotFound:
            location = StockLocation(
                id=location_id,
                warehouse=Reference[Warehouse](key=address.warehouse),
                code=address.location,
            )
            tx.insert(location)
        if location.warehouse.key != address.warehouse or location.code != address.location:
            raise InventoryError("Stored location does not match its coordinate identity")
        value = StockBin(
            id=coordinate_key(address.warehouse, address.location, address.bin),
            warehouse=location.warehouse,
            location=Reference[StockLocation](key=location_id),
            code=address.bin,
        )
        tx.insert(value)
        return value

    return db.atomic(install)


def _address(db: Database, value: StockBin) -> BinAddress:
    location = db.get(StockLocation, value.location.key)
    warehouse = cast(str, value.warehouse.key)
    address = BinAddress(warehouse, location.code, value.code)
    if (
        location.warehouse.key != warehouse
        or location.id != coordinate_key(warehouse, location.code)
        or value.id != coordinate_key(warehouse, location.code, value.code)
    ):
        raise InventoryError("Stored bin does not match its warehouse/location coordinates")
    return address


class BinInventory(InventoryTransactions):
    """The ledger workflows are shared; only stock addressing and queries change."""

    @staticmethod
    def _change(tx: Database, address: BinAddress, lot: Lot, delta: Decimal) -> BalanceChange:
        require_ready(tx)
        bin_ = tx.get(StockBin, coordinate_key(address.warehouse, address.location, address.bin))
        if _address(tx, bin_) != address:
            raise InventoryError("Stored bin does not match the requested address")
        current = bin_.balances.get(lot.id)
        if current is not None and (
            current.lot.key != lot.id or current.product.key != lot.product.key
        ):
            raise InventoryError("Stored balance does not match its lot and product")
        with localcontext() as context:
            context.prec = 40
            quantity = (Decimal(0) if current is None else current.quantity) + delta
        if quantity < 0:
            raise InsufficientStock("Source balance cannot become negative")
        value = Balance(
            id=lot.id, lot=Reference[Lot](key=lot.id), product=lot.product, quantity=quantity
        )
        return BalanceChange(address, value, current is not None)

    def _apply_balances(self, tx: Database, changes: tuple[BalanceChange, ...]) -> None:
        patches = []
        for change in changes:
            address, value = change.address, change.value
            key = coordinate_key(address.warehouse, address.location, address.bin)
            if change.existed:
                patches.append(
                    Patch(
                        StockBin,
                        key,
                        at=("balances", value.id),
                        changes={"quantity": value.quantity},
                    )
                )
            else:
                tx.insert_child(StockBin, key, collection=("balances",), record=value)
        if patches:
            tx.patch_many(patches)

    def stock_at(self, address: BinAddress) -> tuple[StockRow, ...]:
        def read(tx: Database) -> tuple[StockRow, ...]:
            require_ready(tx)
            value = tx.get(
                StockBin, coordinate_key(address.warehouse, address.location, address.bin)
            )
            checked = _address(tx, value)
            return tuple(_row(checked, balance) for balance in value.balances.values())

        return self.db.atomic(read)

    def stock_in_warehouse(
        self,
        warehouse: str,
        *,
        location: str | None = None,
        bin_limit: int = 20,
        after: str | None = None,
    ) -> BinStockPage:
        predicates: dict[str, object] = {"warehouse": Reference[Warehouse](key=warehouse)}
        if location is not None:
            predicates["location"] = Reference[StockLocation](
                key=coordinate_key(warehouse, location)
            )
        return self._page(predicates, bin_limit, after)

    def stock_by_product(
        self, product: str, *, bin_limit: int = 20, after: str | None = None
    ) -> BinStockPage:
        return self._page(
            {"balances.product": Reference[Product](key=product)}, bin_limit, after, product
        )

    def _page(
        self,
        predicates: dict[str, object],
        limit: int,
        after: str | None,
        product: str | None = None,
    ) -> BinStockPage:
        def read(tx: Database) -> BinStockPage:
            require_ready(tx)
            bins = tx.query(StockBin).where(**predicates).all(limit=limit, after=after)
            rows: list[StockRow] = []
            for bin_ in bins:
                address = _address(tx, bin_)
                rows.extend(
                    _row(address, balance)
                    for balance in bin_.balances.values()
                    if product is None or balance.product.key == product
                )
            return BinStockPage(tuple(rows), bins[-1].id if len(bins) == limit else None)

        return self.db.atomic(read)


def main() -> None:
    db = Database(MemoryStore(), Registry(*BIN_MODELS))
    db.insert(Product(id="WIDGET", sku="WDG-001", name="Widget"))
    db.insert(Lot(id="LOT-001", product=Reference[Product](key="WIDGET")))
    db.insert(Warehouse(id="MAIN", name="Main warehouse"))
    dock, storage = BinAddress("MAIN", "DOCK", "B1"), BinAddress("MAIN", "STORAGE", "B1")
    for address in (dock, storage):
        create_bin(db, address)
    inventory = BinInventory(db)
    inventory.receive("R1", "LOT-001", dock, Decimal(20))
    inventory.move("M1", "LOT-001", dock, storage, Decimal(7))
    inventory.adjust("A1", "LOT-001", storage, Decimal("-0.5"), reason="Cycle count")
    print(inventory.stock_in_warehouse("MAIN"))
    print([movement.id for movement in inventory.history("LOT-001")])


if __name__ == "__main__":
    main()
