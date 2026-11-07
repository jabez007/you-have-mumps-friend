# Rebuild indexes

A rebuild repairs equality entries and uniqueness claims from stored records, and keeps the
installed declaration unchanged. It supports declared root and nested indexes, including
reference equality indexes. Existing reverse edges and target metadata get the same
maintenance checks. Reverse-reference counts stay unchanged.

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry


class CatalogProduct(Model):
    __global__ = "^CatalogProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    price: Decimal = Field(default=Decimal("0"), index=True)


db = Database(MemoryStore(), Registry(CatalogProduct))
db.insert(CatalogProduct(id="P1", sku="SKU1", price=Decimal("12.50")))

plan = db.plan_index_rebuild(CatalogProduct)  # All declared indexes by default.
print(plan.to_json())
assert plan.execution_supported
status = db.begin_index_rebuild(CatalogProduct, plan=plan)
job_id = status.migration_id  # Retain this ID for recovery.
while status.phase != "complete":
    status = db.advance_index_rebuild(job_id, CatalogProduct, batch_size=25)
```

## Select indexes

Use `fields=("sku",)` to select particular indexes, or dotted names such as
`fields=("lots.serial", "lots.samples.reading")` for nested indexes. Selections must name
distinct declared indexes.

The review plan is deterministic, reads only schema metadata, and exposes no indexed
values. A model with no indexes has an unsupported plan. So does a declaration that differs
from the installed schema.

## What a rebuild does

A rebuild uses the same [maintenance gate](migrations.md#stop-other-clients-first), schema
barrier, `migration_status`, and `abort_migration` as a schema migration. Raw writers must
stay stopped.

Ordinary access to unrelated models continues. The rebuilt model remains blocked
until activation or safe abort; a second maintenance job is still refused.

The phases are `validate`, `clear_indexes`, `backfill`, `verify`, `activate`, and `complete`.

1. **`validate`** reads authoritative records and reserves selected unique values before
   any index is cleared. Conflicts raise `UniqueViolation`. A rebuild never changes field
   values or picks a surviving record. Missing or invalid record fields fail validation and
   are not filled from defaults.
2. **`clear_indexes`** removes at most `batch_size` leaves per call from the selected
   equality and claim namespaces, including stale and orphan nodes.
3. **`backfill`** reconstructs selected entries in bounded parent pages.
4. **`verify`** checks rebuilt entries and claims.

Unselected namespaces, record bytes, record metadata, and the schema snapshot and
fingerprint stay as they are. The `retagged` counter stays zero. `validated`, `backfilled`,
and `verified` count parent records.

## Resume or abort

Reconstruct a database with the same registry, then resume with
`advance_index_rebuild(job_id, model)`. The selection is persisted with a digest and cannot
change during a resume.

Abort works until the first clearing deletion or reconstruction batch commits. After that,
recovery means resuming or restoring from a backup.

## What a rebuild does not do

A rebuild does not rebuild reverse-reference counts, repair application records, or prune
historical maintenance metadata.

After a job completes or is safely aborted, its retained metadata can be removed with
[`prune_migration`](migrations.md#retire-job-metadata), using the same job ID. Pruning is
explicit and never runs as part of an index rebuild.
