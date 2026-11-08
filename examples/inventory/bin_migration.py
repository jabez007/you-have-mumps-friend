# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit maintenance-window copy/clear; no framework metadata/layout conversion."""

from mccann_hub.yhmf import Database, Reference
from mccann_hub.yhmf.errors import NotFound

from .bin_models import StockBin, StockLocation, StockMoveState, coordinate_key
from .ledger import InventoryError
from .models import Warehouse

MOVE_ID = "bins-v1"


def begin_bin_move(db: Database, *, writers_stopped: bool) -> StockMoveState:
    """Attest that legacy writers are drained; preserve a committed restart checkpoint."""
    if writers_stopped is not True:
        raise InventoryError("Stop all legacy Python/M writers before beginning the stock move")

    def begin(tx: Database) -> StockMoveState:
        try:
            return tx.get(StockMoveState, MOVE_ID)
        except NotFound:
            pass
        if bool(tx.scan(StockLocation, limit=1)) or bool(tx.scan(StockBin, limit=1)):
            raise InventoryError("Stock move requires initially empty location/bin globals")
        state = StockMoveState(id=MOVE_ID, status="copying")
        tx.insert(state)
        return state

    return db.atomic(begin)


def _copy(tx: Database, warehouse: Warehouse) -> None:
    for location in warehouse.locations.values():
        location_id = coordinate_key(warehouse.id, location.id)
        tx.insert(
            StockLocation(
                id=location_id, warehouse=Reference[Warehouse](key=warehouse.id), code=location.id
            )
        )
        for bin_ in location.bins.values():
            tx.insert(
                StockBin(
                    id=coordinate_key(warehouse.id, location.id, bin_.id),
                    warehouse=Reference[Warehouse](key=warehouse.id),
                    location=Reference[StockLocation](key=location_id),
                    code=bin_.id,
                    balances=bin_.balances,
                )
            )


def _clear(tx: Database, warehouse: Warehouse) -> None:
    for key in warehouse.locations:
        tx.delete_child(Warehouse, warehouse.id, at=("locations", key))


def advance_bin_move(db: Database, *, writers_stopped: bool) -> StockMoveState:
    """Atomically move one bounded warehouse; repeat until status is complete.

    A new process may resume with the same registry. Do not resume legacy writers
    between calls. Once any warehouse commits, finish the move or restore a backup;
    this function does not promise a reverse conversion.
    """
    if writers_stopped is not True:
        raise InventoryError("Keep all legacy Python/M writers stopped throughout the stock move")

    def advance(tx: Database) -> StockMoveState:
        state = tx.get(StockMoveState, MOVE_ID)
        if state.status == "complete":
            return state
        warehouses = tx.scan(Warehouse, limit=1, after=state.after)
        if warehouses:
            warehouse = warehouses[0]
            _copy(tx, warehouse)
            _clear(tx, warehouse)
            state = state.model_copy(update={"after": warehouse.id})
        else:
            state = state.model_copy(update={"status": "complete"})
        tx.save(state)
        return state

    return db.atomic(advance)
