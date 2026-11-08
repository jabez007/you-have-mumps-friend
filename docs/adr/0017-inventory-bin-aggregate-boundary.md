# ADR 0017: inventory stock belongs to independently rooted bins

Status: accepted; native timing qualification accompanies the benchmark slice.

## Problem and existing mechanisms

Warehouse v3 owns locations, bins and balances. Its 10,000-node budget is useful for
bounded aggregate replacement and validation, but capacity checks traverse unrelated
structural branches. Adding warehouse bins increases stock-insertion work and consumes
the same budget as stock. Existing partial mutations, root references, reverse indexes,
owned collection limits, and atomic callbacks already support another boundary.

## Decision

Add StockLocation and StockBin roots. A location references Warehouse; a bin references
its location and warehouse, and owns its balances. Its local stored sum uses existing
sum maintenance. Independent roots remove the warehouse-wide stock budget while
retaining a useful native hierarchy for the contents of one bin. Parent lifetimes are
protected by ordinary reference restrictions, including deletion through generic APIs.
The application checks the redundant warehouse/location coordinates before mutations.
Coordinate keys use UTF-8 byte lengths and original values, avoiding delimiter/hash
collisions. Normal native key/index/reference size limits still apply.

BinInventory reuses the receipt/move/adjust transaction workflow and unchanged Movement
v4 records. Historical positions remain snapshots of warehouse/location/bin coordinates,
not references that permanently retain operational bins. Warehouse v3 remains readable;
its old collections and stored sum become empty/zero after an explicit application data
move. They are not current stock reports. No framework metadata conversion is needed.
Legacy Inventory remains available for existing applications and historical benchmarks.
New applications use BinInventory. This is an explicit application API/layout choice.

## Alternatives and costs

A Balance root further bounds single-position reads, but makes bin-local enumeration
and lifetime constraints rely on cross-root indexes. Retaining Warehouse ownership
keeps one aggregate at the cost demonstrated by the capacity walk. The benchmark compares
all three boundaries; timing is evidence for this application, not other databases.

A bin remains bounded by 1,000 balances and 10,000 nodes. There is no warehouse-wide
balance count limit. The service currently loads the selected bin to check coordinate
and lot/product invariants; work can grow with that bin's stock. It never reads unrelated
bins for a stock mutation. Paginated reports bound bin visits, not returned balance rows:
a page of N bins can contain up to N times 1,000 balances. Warehouse/location totals
require explicit page traversal and exact Decimal summation. They are snapshot reports
per page, not an automatically maintained cross-root total.

## Compatibility and data move

Do not open an existing warehouse using the new workflow without an explicit copy and
clear. A maintenance-window application data move checkpoints each warehouse in the
same transaction as copying its locations/bins and clearing its legacy children. Old
Python and M writers must be stopped; the application must not claim online conversion.
Framework Transform intentionally cannot split one root across multiple globals. Keep
existing named transforms and frozen bindings unchanged. The explicit bin_migration entry points implement that copy/clear checkpoint.
Populated databases continue to use the legacy service until operators run it.
