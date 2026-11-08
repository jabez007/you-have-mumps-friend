# Warehouse inventory ledger

Run from the repository root:

```sh
uv sync --locked
uv run python -m examples.inventory.bins
```

The memory demo receives 20 widgets, moves seven from DOCK/B1 to STORAGE/B1, and
records a justified adjustment of -0.5. Final balances are 13 and 6.5, with three
independent movement entries. The demo inserts its catalog and warehouse explicitly;
it does not erase existing data or overwrite a previous run.

With YottaDB installed, the native extra installed, and a database configured:

```sh
uv run python -m examples.inventory.bins --native
```

This writes the declared `^Inventory*` application globals in that database. The test
runner qualifies the same workflows in its disposable database. No web server or
framework is required. These example modules are repository application code;
the installed library remains `mccann_hub.yhmf`.

The original python -m examples.inventory command and Inventory service retain the
legacy Warehouse-owned layout for existing applications and historical benchmarks.
New applications use BinInventory. Existing data needs the explicit stock move below.

## Ownership and references

| Model | Identity and relationship | Reason |
| --- | --- | --- |
| Product | Independent string key and unique SKU | Catalog identity survives stock movement. |
| Lot | Independent key; references Product | Balances and history share a stable lot. |
| Warehouse | Independent key | Coordinate/lifetime parent for locations. |
| StockLocation | Independent coordinate key; references Warehouse | Location lifetime is protected by bin references. |
| StockBin | Independent coordinate key; references StockLocation and Warehouse | Owns one bounded stock hierarchy, independently of unrelated bins. |
| Balance | StockBin-owned lot key; references Lot and Product | Quantities belong at an exact physical position. |
| Movement | Independent caller-supplied key; references Lot, Product, and positions' Warehouses | History remains addressable independently of current balances. |
| Position | Embedded warehouse reference and location/bin names | A movement preserves its historical position without retaining operational bins through permanent references. |
| Supplier | Independent key; owns product memberships | Removing a supplier removes its memberships while products remain independent. |
| SupplierProduct | Supplier-owned product key; references Product | Each supplier/product pair has one native owned link, with a maintained inverse edge. |

The balance's product reference is intentional denormalization for indexed stock
queries. The inventory transaction checks it against the lot's product before
changing that balance. YHMF validates references and ownership; this application
service enforces cross-record inventory rules. Writing directly through `Database`
can bypass those business rules. Historical movement positions are snapshots:
warehouse references are enforced, while location/bin strings preserve history.

The service has no authenticated principal and does not authorize callers. An
application must restrict access through its own trusted boundary; neither this
example nor Python mapper checks constrain independent M writers. See the
[authorization decision](../../docs/adr/0008-application-authorization-boundary.md).

The models reuse an unregistered Identified base for their nonempty string id.
Concrete roots still declare their own global, and Position stays an embedded value
without its own key. This changes no existing schema bytes or stored paths. See
[the model-reuse decision](../../docs/adr/0007-compile-time-model-reuse.md).

In the legacy layout, Warehouse.total_on_hand is a virtual sum of that loaded warehouse's balances. It
creates no stored rollup nodes and requires no schema migration. It does not query
fresh data or replace bounded balance queries; load an aggregate only when the
workflow needs it. The uncached calculation uses its own Decimal precision.
Warehouse.stored_on_hand is a separate indexed Decimal sum maintained with its
owned quantities. The demo prints both totals. This stored field requires warehouse
schema revision two. Location.stored_on_hand adds a local indexed total in warehouse
revision three; existing warehouse data needs the explicit upgrade below. Reading
one location verifies its total without loading other locations.

Owned reads in the service use Database.path selectors, so strict mypy infers Balance,
Bin, and Location without casts. BinAddress remains the domain address for stock
operations. [typing_example.py](typing_example.py) records positive inference checks;
the test suite runs mypy against deliberately invalid paths and reference keys.

## Workflows

```python
from decimal import Decimal
from examples.inventory import MODELS, BinAddress, Inventory, Warehouse
from examples.inventory.__main__ import seed
from mccann_hub.yhmf import Database, MemoryStore, Registry

db = Database(MemoryStore(), Registry(*MODELS))
seed(db)
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
assert db.get(Warehouse, "MAIN").stored_on_hand == Decimal("19.5")
assert db.get(Warehouse, "MAIN").stored_on_hand == db.get(Warehouse, "MAIN").total_on_hand
```

Each command reads current balances, applies changes, and inserts the movement
inside one `db.atomic` callback. Existing balances use bounded owned-path `get`
and patch_many; new balances use insert_child and retain its aggregate-size check.
Existing sides of a move share one patch group. Transfers within one warehouse
have a net-zero sum delta, so their stored total and total index are untouched.
Stock cannot become negative. Quantities are finite Decimals with at most twelve
whole and six fractional digits. Arithmetic is independent of the caller's Decimal
precision, including the negation used for debits.

Movement IDs and timestamps are fixed before retryable persistence work. A duplicate
movement ID raises `AlreadyExists` and rolls back the complete command, including
both sides of a move. This is duplicate protection, not automatic idempotent success.
Commands generate a UTC timestamp before entering the callback when none is supplied;
explicit ISO strings and datetime inputs must include a timezone. Movement.occurred_at
now returns and stores a UTC datetime. Other timezones normalize to the same instant;
the original zone is not retained.

The service only appends movement records and their Python values are frozen.
It does not provide database permissions or prevent another program from replacing
history through the general persistence API. Use this service as the application's
write boundary. Custom business-rule changes require an application schema revision
when they change persistence semantics, as described in the schema compatibility policy.

## Upgrade existing warehouses

Warehouse revision three adds indexed Location.stored_on_hand over bins.balances.quantity,
beside the revision-two warehouse total over locations.bins.balances.quantity.
Opening existing data does not initialize zero
or rewrite its schema. The frozen LegacyWarehouse/LegacyLocation/LegacyBin/
LegacyBalance declarations preserve the original golden contract. WarehouseV2
preserves the root-only total contract and WAREHOUSE_TOTALS keeps its original v2
target and transform name for interrupted historical jobs. WAREHOUSE_TOTALS_V3
uses the normal named-transform lifecycle to derive real totals and build their
index. Existing quantity bytes and incoming reference identities remain unchanged;
the transform can reapply unchanged field nodes while rewriting each aggregate.

```python
from examples.inventory.migrations import LegacyWarehouse, WAREHOUSE_TOTALS_V3

warehouse_plan = db.plan_migration(Warehouse, transform=WAREHOUSE_TOTALS_V3)


def upgrade_warehouses(current, reviewed_plan, migration_id=None):
    status = (
        current.begin_migration(
            LegacyWarehouse, Warehouse, plan=reviewed_plan, transform=WAREHOUSE_TOTALS_V3
        )
        if migration_id is None
        else current.migration_status(migration_id)
    )
    while status.phase != "complete":
        status = current.advance_migration(
            status.migration_id, LegacyWarehouse, Warehouse,
            transform=WAREHOUSE_TOTALS_V3, batch_size=25,
        )
    return status


# For existing v1 warehouses, review the plan/recovery boundary before calling:
# result = upgrade_warehouses(db, warehouse_plan)
```

For an installed revision-two database, use WarehouseV2 as the source binding in
the same lifecycle with WAREHOUSE_TOTALS_V3. Finish an interrupted v1-to-v2 job with
its frozen bindings and original WAREHOUSE_TOTALS before starting the next job.

Fresh demo data uses revision three. Keep the job id for restart recovery,
coordinate raw writers, and use the existing transform abort/resume boundary. The
virtual total_on_hand remains a loaded-object calculation and is not stored. Whole
warehouse reads verify the stored total; child mutations do not read sibling balances.
The warehouse total adds intentional contention for writers affecting one warehouse;
location totals add shared nodes for writers within each location. They demonstrate
the reporting tradeoff rather than a recommendation to store totals at every level.
Generated M writers can create an empty warehouse, patch its scalars, insert, patch,
and delete locations, bins, and balances, and delete a populated warehouse that no
movement or independent bin references. Each call
maintains both totals, counts, indexes, and references in one TP. Python still
constructs or replaces a populated warehouse in one call. SupplierProduct's custom
validator keeps Supplier out of the M exporter. Movement exports, and the
[M receipt](#receive-stock-from-m) applies the receive workflow's business checks;
move and adjust remain Python services. See the
[M guide](../../docs/guides/m-access.md#mutate-a-keyed-hierarchy). Raw SET/KILL cannot maintain
the total or its index. See [computed fields](../../docs/guides/computed-fields.md).

## Upgrade an existing ledger

Movement schema revision two changes occurred_at from string to datetime; revision
three adds an ordered timestamp index for bounded newest-first reports. Revision four
uses finite receive/move/adjust kinds. Opening an
older database with current MODELS does not convert its data; ledger access fails with
SchemaMismatch until an explicit migration. Warehouse revision two independently
adds the stored total; upgrade existing warehouses before resuming ledger writes
or migrating v1 movement timestamps.
The frozen source declarations and named conversion live in
[migrations.py](migrations.py), with the original schema fixture protecting their bytes.

On a current Database handle, construct and review the plan, then use the normal
maintenance lifecycle. This helper shows both a new job and resume by retained ID:

```python
from examples.inventory import Movement
from examples.inventory.migrations import LegacyMovement, MOVEMENT_TIMESTAMPS_V4

ledger_plan = db.plan_migration(Movement, transform=MOVEMENT_TIMESTAMPS_V4)


def upgrade_ledger(current, reviewed_plan, migration_id=None):
    status = (
        current.begin_migration(
            LegacyMovement, Movement, plan=reviewed_plan, transform=MOVEMENT_TIMESTAMPS_V4
        )
        if migration_id is None
        else current.migration_status(migration_id)
    )
    while status.phase != "complete":
        status = current.advance_migration(
            status.migration_id, LegacyMovement, Movement,
            transform=MOVEMENT_TIMESTAMPS_V4, batch_size=25,
        )
    return status


# For existing v1 data, review ledger_plan and the recovery boundary before calling:
# result = upgrade_ledger(db, ledger_plan)
```

The fresh demo uses revision four. Existing v3 data uses frozen MovementV3 and the
separately named MOVEMENT_KINDS_V4 transform. Review plan_migration(Movement,
transform=MOVEMENT_KINDS_V4), then run begin/advance with MovementV3 as source.
Record/index/reference value bytes remain unchanged; the explicit migration validates
the finite domain and updates metadata. A plan without the transform is unsupported.

Keep original job bindings when recovering: UTC-v2 uses MovementV2 with
MOVEMENT_TIMESTAMPS; UTC-ordered-v3 uses frozen MovementV3 with
MOVEMENT_TIMESTAMPS_V3. Finish that job before starting kinds-v4. A v2-to-v3 ordered
index migration still uses MovementV2 to MovementV3 without a transform. The v4
transform can also upgrade v2 after review. No existing transform name was retargeted
to the new active schema, and opening a database never converts it automatically.
For an
existing v1 ledger, retain the job ID and coordinate raw readers/writers before
starting. Invalid, naive, or out-of-calendar historical timestamps fail the validation
pass without changing records; early abort remains available. Later committed work
requires resume or backup restore, as the [transform guide](../../docs/guides/record-transforms.md)
explains. Migration tests preserve stock data, references, indexes, and ledger IDs
across interruption, and compare UTC instants after activation.

For a bounded newest-first lot report, use its reference predicate and timestamp
index. Root pages return each movement once on stable data, including timestamp
ties ordered by descending root key:

```python
from examples.inventory import Lot
from mccann_hub.yhmf import Reference

occurred = db.field(Movement, lambda movement: movement.occurred_at)
recent = db.query(Movement).where(lot=Reference[Lot](key="LOT-001")).order_by(
    occurred, direction="desc"
)
recent_page = recent.page(limit=20)
assert recent.explain().order_direction == "desc"
# Continue with recent.page(after=recent_page.cursor) while cursor is not None.
```

Progress pages can contain no records; follow their cursor until None. Independent
query predicates still match under the root rather than implying a child join.

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

Read matching balances directly when a complete warehouse is unnecessary:

```python
matches = query.occurrences(limit=25)
assert len(matches) == 2
assert all(hit.record.product.key == "WIDGET" for hit in matches)
assert {hit.at[1] for hit in matches} == {"DOCK", "STORAGE"}
assert query.occurrences(limit=25, after=matches[-1].cursor) == []
```

Each result contains a validated Balance, its warehouse `root_key`, and the concrete
`at` path. This preserves the original `stock_by_product` root-page interface while
providing a public query alternative that reads only matching balance owners. Its cursor
pages occurrences rather than warehouses. See the [query contract](../../docs/guides/indexes-and-queries.md).

## Products and suppliers

Supplier memberships form a many-to-many relation through owned reference links:

```python
from examples.inventory import Supplier, SupplierProduct

for supplier in ("S1", "S2"):
    db.insert(Supplier(id=supplier, name=supplier))
    db.insert_child(
        Supplier, supplier, collection=("products",),
        record=SupplierProduct(id="WIDGET", product=Reference[Product](key="WIDGET")),
    )
first = db.referrers(Reference[Product](key="WIDGET"), source=Supplier, limit=1)
assert first[0].root_key == "S1" and first[0].at == ("products", "WIDGET")
second = db.referrers(
    Reference[Product](key="WIDGET"), source=Supplier, limit=1, after=first[-1].cursor
)
assert second[0].root_key == "S2"
db.delete_child(Supplier, "S1", at=("products", "WIDGET"))
assert db.get(Product, "WIDGET").id == "WIDGET"
```

The membership validator binds its child key to its product reference. A supplier owns
only the link, so removing it releases the reverse edge without deleting the product.
Reverse entries provide the inverse lookup directly; no additional relation storage is
needed. These optional models use a new Supplier global and leave the existing product,
warehouse, lot, and movement schemas unchanged.

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
- `.explain()` identifies the chosen index and bound. The framework now offers
  direct occurrence projections; stock_by_product keeps its warehouse-key page
  contract and loads matching roots. Use occurrences explicitly for balance pages.
- Reference counts for new movement entries touch shared Product/Lot/Warehouse
  targets. They provide deletion safety but can become contention points. The
  example does not claim independent stock updates eliminate that shared work.
- Building and checking a downstream package exposed mypy's namespace-root
  ambiguity. The project now checks both source and example with an explicit
  namespace base, while preserving the `mccann_hub` namespace package.

See [inventory performance](../../docs/inventory-performance.md) for equivalent
workflow benchmarks, the direct native lower bound, and measurement limitations.

### Receive stock from M

After explicitly upgrading Movement to revision 4, export WarehouseAPI and MovementAPI
with db.m_routine using the inventory registry. Put both generated sources and
[YHMFRECEIPT.m](YHMFRECEIPT.m) on the M routine path. This application routine owns a
restartable TSTART() and composes insertchildtx/patchchildtx with inserttx. It checks
lot/product identity and recomputes stock on every retry. Supply stable request IDs
and UTC timestamps before calling it:

```mumps
 set input("id")="receipt-001",input("lot")="LOT-001",input("product")="WIDGET"
 set input("warehouse")="MAIN",input("location")="DOCK",input("bin")="B1"
 set input("quantity")="2.5",input("reason")="delivery"
 set input("occurred_at")="2026-11-07T10:00:00.000000Z"
 write $$receive^YHMFRECEIPT(.input),!
```

All six identifiers must be nonempty UTF-8 strings of at most 64 bytes without NUL.
The quantity is positive and satisfies Quantity's Decimal constraints. A duplicate
movement returns exists and rolls back the stock update; it does not silently treat
a different request as a replay. Other validation/participant failures also roll back.
An engine error propagates after rollback. Nested invocation returns nested.

This example intentionally reads the documented tagged stock/lot nodes directly.
It uses exact arithmetic labels from these generated sources; regenerate the pair
and requalify the application together after framework/schema upgrades. Those helper
labels are not a new framework public arithmetic API. Generic M stock mutations do
not automatically create audit history: use this application transaction when a
receipt must have a ledger entry. Forced-restart and competing-receipt native tests
check the complete stock, totals, indexes, reverse references, and timestamp index.

## Independently rooted bins

New applications can run python -m examples.inventory.bins and use BIN_MODELS,
StockLocation, StockBin, create_bin and BinInventory from the bin_models/bins modules.
Receipt, move, adjust and history retain the existing service arguments. Stock is
owned under each StockBin root. Locations reference Warehouse and bins reference both
parents, so deleting a parent with live bins is restricted. Movement keeps its immutable
historical coordinates. [ADR 0017](../../docs/adr/0017-inventory-bin-aggregate-boundary.md)
explains the boundary and the Balance-root alternative.

A bin can hold at most 1,000 balances and 10,000 nodes. A warehouse can have independent
bins beyond its former aggregate budget; the regression fixture stores 1,600 balances
across two bins. Stock writes inspect their selected bin and location, so their work
can grow with balances in that bin, but does not grow with unrelated warehouse bins.
Coordinate keys are inspectable length-prefixed UTF-8 strings; normal native key and
index size limits still apply. There is no automatic key hashing or truncation.

stock_in_warehouse and stock_by_product now return BinStockPage in this new service.
Pass bin_limit and then after=page.next_bin until next_bin is None. A page visits at
most bin_limit bins and can return at most bin_limit times 1,000 stock rows. Sum pages
explicitly for warehouse/location reports; concurrent changes between pages are visible.
The legacy Warehouse.stored_on_hand reports only legacy owned stock. Do not use it as
a cross-root total. The original Inventory service and historical benchmark retain
their original warehouse layout. Existing populated databases require an explicit
maintenance-window move before using the new layout; no automatic conversion occurs.

### Explicit stock layout move

Back up the database and stop **all** legacy Python/M stock and setup writers for the
whole procedure. This is a maintenance-window application data move; it does not stop
external processes for you. Register BIN_MODELS, then call begin_bin_move(db,
writers_stopped=True) from examples.inventory.bin_migration. It rejects initially
nonempty destination globals. Repeat advance_bin_move(db, writers_stopped=True) until
state.status == "complete". Do not resume legacy writers between calls.

Each call copies one warehouse's locations/bins and balances, clears that warehouse's
old children, and advances the checkpoint in one native transaction. Existing warehouse,
product, lot and movement identities remain unchanged. A failed call rolls back both
layouts and its checkpoint. After a process restart, reconstruct Database with the same
registry and continue. Concurrent cooperating workers serialize through the checkpoint.
Repeated completion is idempotent. Once a warehouse commits, finish the move or restore
your backup; no reverse conversion is promised. Only then enable BinInventory/new M
writers and retire the old stock writers. BinInventory refuses stock reads/writes while
the move is incomplete. History remains readable. No schema snapshot, fingerprint,
original named transform, or application record outside the moved ownership changes.

After the bin move, also export StockBin as BinAPI and StockMoveState as StockMoveAPI
from BIN_MODELS. Keep WarehouseAPI/MovementAPI alongside them, and call
$$receivebin^YHMFRECEIPT(.input) with the same request fields. It uses length-prefixed
coordinate keys and bin participants, validates the managed move checkpoint, and
returns maintenance while that checkpoint is incomplete. The receive entry retains
the original warehouse-owned addressing and must be retired after the move.
Requalify/regenerate all four application bindings together after schema upgrades.

Use python -m examples.inventory.bins --native only with a fresh disposable native
database configured through the usual YottaDB environment. It seeds the same receipt,
transfer and adjustment workflow as the memory example. For example, native stock is
inspectable at ^InventoryBin("s:4:MAIN4:DOCK2:B1","balances","s:LOT-001","quantity").
The script does not clear existing application data or adopt unmanaged globals.

The move's per-warehouse transaction must also fit the native database's transaction
buffers and encoded key/index limits. A native buffer or key-size error rolls back
that warehouse and retains the previous checkpoint. Stop and resolve that constraint
before resuming; the mover does not split a warehouse into partially committed copies.
