# Warehouse inventory ledger

Run from the repository root:

```sh
uv sync --locked
uv run python -m examples.inventory
```

The memory demo receives 20 widgets, moves seven from DOCK/B1 to STORAGE/B1, and
records a justified adjustment of -0.5. Final balances are 13 and 6.5, with three
independent movement entries. The demo inserts its catalog and warehouse explicitly;
it does not erase existing data or overwrite a previous run.

With YottaDB installed, the native extra installed, and a database configured:

```sh
uv run python -m examples.inventory --native
```

This writes the four `^Inventory*` application globals in that database. The test
runner qualifies the same workflows in its disposable database. No web server or
framework is required. These example modules are repository application code;
the installed library remains `mccann_hub.yhmf`.

## Ownership and references

| Model | Identity and relationship | Reason |
| --- | --- | --- |
| Product | Independent string key and unique SKU | Catalog identity survives stock movement. |
| Lot | Independent key; references Product | Balances and history share a stable lot. |
| Warehouse | Independent key | Owns its physical storage hierarchy. |
| Location | Warehouse-owned key | Location names are meaningful within a warehouse. |
| Bin | Location-owned key | The same bin name can occur in different locations. |
| Balance | Bin-owned lot key; references Lot and Product | Quantities belong at an exact physical position. |
| Movement | Independent caller-supplied key; references Lot, Product, and positions' Warehouses | History remains addressable independently of current balances. |
| Position | Embedded warehouse reference and location/bin names | A movement preserves its historical position without creating independently stored bins. |

The balance's product reference is intentional denormalization for indexed stock
queries. The inventory transaction checks it against the lot's product before
changing that balance. YHMF validates references and ownership; this application
service enforces cross-record inventory rules. Writing directly through `Database`
can bypass those business rules. Historical movement positions are snapshots:
warehouse references are enforced, while location/bin strings preserve history.

## Workflows

```python
from decimal import Decimal
from examples.inventory import BinAddress, Inventory

inventory = Inventory(db)
dock = BinAddress("MAIN", "DOCK", "B1")
storage = BinAddress("MAIN", "STORAGE", "B1")

inventory.receive("0001", "LOT-001", dock, Decimal("20"))
inventory.move("0002", "LOT-001", dock, storage, Decimal("7"))
inventory.adjust("0003", "LOT-001", storage, Decimal("-0.5"), reason="Damaged stock")

stock = inventory.stock_at(storage)
warehouse_stock = inventory.stock_in_warehouse("MAIN", location="STORAGE")
page = inventory.stock_by_product("WIDGET", warehouse_limit=20)
history = inventory.history("LOT-001", limit=100)
```

Each command reads current balances, applies changes, and inserts the movement
inside one `db.atomic` callback. Existing balances use bounded owned-path `get`
and `patch`; new balances use `insert_child` and retain its aggregate-size check.
Stock cannot become negative. Quantities are finite Decimals with at most twelve
whole and six fractional digits. Arithmetic is independent of the caller's Decimal
precision, including the negation used for debits.

Movement IDs and timestamps are fixed before retryable persistence work. A duplicate
movement ID raises `AlreadyExists` and rolls back the complete command, including
both sides of a move. This is duplicate protection, not automatic idempotent success.
Commands generate a UTC timestamp before entering the callback when none is supplied;
an explicit ISO timestamp must include a timezone. Timestamp fields remain strings
because YHMF does not yet have date/time codecs.

The service only appends movement records and their Python values are frozen.
It does not provide database permissions or prevent another program from replacing
history through the general persistence API. Use this service as the application's
write boundary. Custom business-rule changes require an application schema revision
when they change persistence semantics, as described in the schema compatibility policy.

## Queries and bounds

Stock by product uses `locations.bins.balances.product`; lot history uses the
movement's `lot` index. Warehouse and location queries use direct keys and owned
paths. All use the public API. `stock_by_product` pages warehouse roots; its
`next_warehouse` is passed as `after` on the next call. A full last page can return
a cursor followed by an empty page. History pages by movement key, so use sortable
IDs if chronological order is required. It does not sort timestamps automatically.

An indexed warehouse query still materializes each matching warehouse. It returns
matching balances from those bounded aggregates; it is not an occurrence projection.
Use `stock_at` for a specific bin. A page's reads share a transaction; separate
pages can observe intervening writes. Existing child-count and record-node limits
bound each aggregate. Zero balances remain present after a complete transfer.

Inspect a query before executing it:

```python
from examples.inventory import Product, Warehouse
from mccann_hub.yhmf import Reference

query = db.query(Warehouse).where(
    **{"locations.bins.balances.product": Reference[Product](key="WIDGET")}
)
print(query.explain())
```

## Native inspection

The demo exposes ordinary nodes such as:

```text
^InventoryWarehouse("s:MAIN","locations","s:STORAGE","bins","s:B1","balances","s:LOT-001","quantity") = "d:6.5"
^InventoryMovement("s:0003-adjust","quantity") = "d:-0.5"
```

The framework's `%m`, schema, index, and reference globals remain inspectable too.
The application does not access them directly. Tests inspect them to verify rollback,
native restarts, and reference/index maintenance. A coordinated native process test
competes to move 15 units twice from a balance of 20: only one command may commit.

## Developer-experience findings

- Typed ownership and explicit atomic operations fit the workflows. Keeping
  Product/Lot independent avoids copying catalogs into every warehouse.
- Repeating an owned path is cumbersome. The local `BinAddress` value object
  centralizes this application's location terminology without adding a framework
  path DSL. Typed reads still need casts because owned `get` returns `Model`.
- A first attempt at `.where(product=...)` on Warehouse fails because the actual
  indexed field is deeply owned. The missing-index error now lists the primary key
  and actual indexed declaration paths, including the usable product path. This
  improvement has a failing regression against the original generic message and
  does not expose predicate values or add implicit scans.
- `.explain()` identifies the chosen index and bound, but indexed aggregate reads
  still load unrelated balances. Occurrence projections are a measured follow-up,
  rather than an implicit change to query semantics.
- Reference counts for new movement entries touch shared Product/Lot/Warehouse
  targets. They provide deletion safety but can become contention points. The
  example does not claim independent stock updates eliminate that shared work.
- Building and checking a downstream package exposed mypy's namespace-root
  ambiguity. The project now checks both source and example with an explicit
  namespace base, while preserving the `mccann_hub` namespace package.
