# Index and query records

YHMF only runs queries that have a bounded plan. An unindexed filter fails instead of
scanning the global.

## Declare an index

`index=True` adds an equality index. `unique=True` adds an equality index and a uniqueness
claim:

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry
from mccann_hub.yhmf.errors import UniqueViolation


class CatalogProduct(Model):
    __global__ = "^CatalogProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    price: Decimal = Field(default=Decimal("0"), index=True)


db = Database(MemoryStore(), Registry(CatalogProduct))
db.insert(CatalogProduct(id="P1", sku="SKU1", price=Decimal("12.50")))
try:
    db.insert(CatalogProduct(id="P2", sku="SKU1"))
except UniqueViolation:
    pass
```

Index rules:

- A unique field allows several `None` values, and their equality entries stay queryable.
- Strings compare exactly and case-sensitively.
- Numerically equal decimals and floating-point zero share index values. Stored decimal
  precision stays unchanged.
- You can index scalar or reference fields, including those inside embedded objects and
  owned children. Indexing a whole container is rejected.
- A nested unique field is unique across all occurrences at that declaration path under
  the top-level model, including children of different parents.

Every insert, replacement, and deletion updates indexes and uniqueness claims in the
record's transaction. Constraints apply after each write, not at commit. Changing or
deleting an indexed value releases its old claim.
Values are never truncated or hashed to fit. An oversized native index key raises an
error before any record write. See the [index key lengths](../reference/limits.md#index-values).

Adding an index to a model with data requires a [migration](migrations.md).

## Run a query

```python
query = db.query(CatalogProduct).where(price=Decimal("12.5"))
assert query.explain().strategy == "equality_index"
assert [product.id for product in query.all(limit=25)] == ["P1"]
assert query.all(limit=25, after="P1") == []
assert db.query(CatalogProduct).where(id="P1").explain().strategy == "direct_key"
```

A query accepts one equality predicate on a declared index or the primary key. An
unindexed or unknown field raises `QueryError`. Filter values must satisfy the declared
field type and constraints.

> **Warning:** `where` does not apply the model's configuration to the filter value. On a
> model with `str_to_lower`, `where(email=" Me@X.org ")` returns nothing, while the stored
> value is `me@x.org`. Pass the normalized value. See
> [known limitations](../known-limitations.md).

## Page through results

`all` defaults to 100 root records and accepts limits from 1 through 1000. Results use
native primary-key order. Pass the last returned key as `after` to continue. Each page
uses its own transaction, so pages do not share a snapshot.

To list records without a filter, ask for a bounded page explicitly:

```python
assert [product.id for product in db.scan(CatalogProduct, limit=25)] == ["P1"]
```

## Query inside owned trees

Use dotted declaration paths such as `lines.product`, passed as a dictionary to `where`:

```text
db.query(Order).where(**{"lines.product": Reference[Product](key="P42")})
```

Such a query returns each matching owning root once, even when several children match.
It does not return the children themselves.

## Explain and check

`explain` reports a `direct_key` or `equality_index` plan, without filter values.

`check_indexes` checks expected secondary entries and unique claims for a bounded page
of records:

```python
report = db.check_indexes(CatalogProduct, limit=25)
assert report.issues == ()
```

Its issues contain field names and error kinds, without indexed values. A full page
returns `next_key`, and the next page may be empty.

This audit does not search all index entries for orphans, and it does not repair data.
Indexed queries reject any orphan entry they meet, and any entry whose value disagrees
with its record. To repair entries, [rebuild the index](index-rebuilds.md).
