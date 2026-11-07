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

## Ordered index storage

Field(ordered=True) opts a supported scalar into the versioned ordered-v1 index
contract and implies index=True. unique=True can be combined with it:

```python
class PricePoint(Model):
    __global__ = "^PricePoint"
    id: str = Field(primary_key=True)
    price: Decimal = Field(ordered=True, unique=True, ge=0, max_digits=18, decimal_places=6)


prices = Database(MemoryStore(), Registry(PricePoint))
prices.insert(PricePoint(id="A", price=Decimal("12.50")))
assert prices.query(PricePoint).where(price=Decimal("12.5")).all()[0].id == "A"
assert prices.plan_index_rebuild(PricePoint).to_dict()["indexes"][0]["codec"] == "ordered-v1"
```

These physical index values support explicit range, literal-prefix, and ascending
ordered queries. M can also walk the value subscripts in native order. Record scalar bytes and
their precision/scale remain unchanged; unique claims use the same canonical index
value as equality lookup. Noncanonical aliases cannot make separate valid claims.

Supported domains are str, int, Decimal, date, aware datetime, and str/int finite
choices. Integers/Decimals must have at most 18 significant digits; nonzero magnitudes
must be at least 1E-18 and below 1E18. Unsupported numeric values fail before writes,
with no rounding or fallback. Zero is canonical and signed/scaled zeros share a
claim. Floats, bools, bytes, UUIDs, references, and lists retain ordinary equality
indexes. Strings preserve Unicode/control values through order-preserving escaping;
native key-size limits still apply. The contract is qualified with standard native
collation. Nulls use a distinct last subscript and create no unique claim.

An additive ordered index on an unindexed field uses normal migration. Changing an
existing index codec requires an explicit named record transform initially; it
rebuilds indexes and reapplies the target record through existing bounded recovery.
This can rewrite unchanged field bytes. There is no new index-conversion framework.
Domain failures leave their checkpoint and barrier intact; inspect and explicitly
abort an early additive job or correct its input/binding before resuming. Automatic
abort retains its existing validation-only semantics. Ordinary index rebuilds
preserve the installed codec and include it in their review output.

## Range, prefix, and ascending order

```python
prices.insert(PricePoint(id="B", price=Decimal("25")))
selected = prices.query(PricePoint).range("price", ge=Decimal("10"), lt=Decimal("20"))
assert [point.id for point in selected.all()] == ["A"]
assert selected.explain().strategy == "ordered_index"
assert selected.explain().maximum_index_visits == 1000
assert [point.id for point in prices.query(PricePoint).order_by("price").all()] == ["A", "B"]
first = selected.occurrences(limit=1)[0]
assert selected.occurrences(after=first.cursor) == []
```

range requires at least one endpoint; gt/ge and lt/le are exclusive/inclusive lower
and upper bounds. Endpoints use the field's adapter, normalization, constraints, and
ordered numeric limits. Numeric and date/time ranges follow native value order;
ties follow native root-key order and location digest. Nulls are excluded from
ranges and sort last in order_by. An inverted range fails clearly.

prefix(field, text) requires an ordered string field and treats text as a literal
prefix of the stored value. It intentionally does not apply full-value length,
pattern, or normalization rules. For normalized fields, supply the stored prefix.
An empty prefix includes every non-null string. Prefix and range reads seek directly
in the declared index; they never enumerate the application global.

All these calls bound execution to 1000 combined value-subscript and occurrence
visits. Each value group costs one visit, and each concrete occurrence another.
If this bound is exhausted before satisfying the requested limit or completing the
walk, QueryError is raised and no incomplete result is returned as a success.
Narrow the selection or use smaller occurrence pages. The result limit remains
1–1000. Limits bound returned items; they do not authorize an unbounded search.

Ordered all() returns distinct roots, ranked by the first matching occurrence, and
materializes those complete aggregates. It starts from the beginning on each call
and rejects key-based after cursors. Use occurrences(limit=..., after=...) for
continuation and efficient child-owner projections. Its version-two cursor records
the ordered value and concrete location, and is scoped to the schema and full
selection. It remains usable when that value/owner has been deleted. Cursors expose
encoded positions, are not encrypted, and do not freeze a snapshot across calls.

Existing equality queries retain their root-key and version-one occurrence cursors.
Descending order, root continuation in value order, and mixed predicates are not
part of this slice. An unsupported plan fails rather than sorting an unbounded
scan in Python.

## General index rules

- A unique field allows several `None` values, and their equality entries stay queryable.
- Strings compare exactly and case-sensitively.
- Numerically equal decimals and floating-point zero share index values. Stored decimal
  precision stays unchanged.
- You can index scalar or reference fields, including those inside embedded objects and
  owned children. Scalar lists have whole-list equality indexes; owned object and child
  containers cannot be indexed as one value.
- A nested unique field is unique across all occurrences at that declaration path under
  the top-level model, including children of different parents.

Every insert, replacement, and deletion updates indexes and uniqueness claims in the
record's transaction. Constraints apply after each write, not at commit. Changing or
deleting an indexed value releases its old claim.
Use an explicit [patch group](#exchange-unique-values) to exchange unique values.
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

`where` applies the field's declared constraints and its owning model's configuration,
including string normalization and strictness. A nested field uses its owned model's
configuration. A primary-key predicate applies the same rules; `get` continues to use
an exact stored key. Reference values keep the reference model's own configuration.

Predicates validate one field, including its `Annotated` validation metadata. They do not
run decorator-based `field_validator` methods or model validators that require a complete
record. For those transformations, pass the stored value.

## Page through results

`all` defaults to 100 root records and accepts limits from 1 through 1000. Results use
native primary-key order. Pass the last returned key as `after` to continue. Each page
uses its own transaction, so pages do not share a snapshot.

To list records without a filter, ask for a bounded page explicitly:

```python
assert [product.id for product in db.scan(CatalogProduct, limit=25)] == ["P1"]
```

## Query inside owned trees

`all()` returns each matching root once. Use `occurrences()` to read the complete owner
of each indexed occurrence, without loading its unrelated ancestors or siblings:

```python
hits = query.occurrences(limit=25)
assert len(hits) == 1 and hits[0].root_key == "P1"
assert hits[0].at == ()  # This index is on the root CatalogProduct model.
assert hits[0].record.price == Decimal("12.50")
assert query.occurrences(limit=25, after=hits[-1].cursor) == []
```

For an owned field such as `bins.lots.quantity`, `record` is its Lot owner and `at`
contains the concrete path, such as `("bins", "A", "lots", "L1")`. `root_key` identifies
the warehouse; `field` is the dotted indexed declaration. The indexed field's complete
owner is validated. If that owner has descendants, its subtree is loaded too. An index
on a root field therefore still loads that root. Primary-key lookups use `get` or `all`;
occurrence projection requires a declared index.

Limits are 1 through 1000 occurrences, default 100. Order is native root-key order, then
the stored location digest; it is not child-key or value order within a root. Pass the
last result's opaque `cursor` to continue. Cursors belong to one installed schema and
predicate, and a deleted cursor occurrence does not prevent continuation. They contain
encoded record positions, are not encrypted, and do not provide a snapshot across calls.
Concurrent insertions before a cursor can be omitted and later insertions can appear.
For a shared snapshot, page inside one `atomic` callback.

Every projection checks the exact location and its digest, owner identity, field value,
equality entry, and applicable unique claim. A stale entry cannot be justified by another
matching child under the same root. Root queries also verify their concrete equality
entries. Corruption raises `StorageError`; neither interface repairs data during a read.

Use dotted declaration paths such as `lines.product`, passed as a dictionary to `where`:

```text
db.query(Order).where(**{"lines.product": Reference[Product](key="P42")})
```

Such a query returns each matching owning root once, even when several children match.
It does not return the children themselves.

## Explain and check

`explain` reports a `direct_key`, `equality_index`, or `ordered_index` plan without
filter values, including the visit bound for an ordered walk.

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

## Exchange unique values

Use an explicit group when several changes must release and reclaim unique values
together. Continuing the catalog example:

```python
from mccann_hub.yhmf import Patch

db.insert(CatalogProduct(id="P2", sku="SKU2"))
db.patch_many([
    Patch(CatalogProduct, "P1", changes={"sku": "SKU2"}),
    Patch(CatalogProduct, "P2", changes={"sku": "SKU1"}),
])
assert db.get(CatalogProduct, "P1").sku == "SKU2"
assert db.get(CatalogProduct, "P2").sku == "SKU1"
assert db.check_indexes(CatalogProduct).issues == ()
```

patch_many accepts a finite sequence of 1–100 Patch values. A Patch has the same
model, key, at, and changes meanings as patch. Groups may span independent roots
and owned children. Combine all changes for one owner into one Patch; duplicate
owner addresses are rejected. Input is copied before the first attempt's validation
and retained across retries of this call.

Each owner is validated using the ordinary partial-update rules. The group releases
old claims and acquires final claims in one transaction; duplicates within the final
state still fail. Reference checks, inverse edges, indexes, and data commit together.
Only changed field nodes are written. No arbitrary callback runs between group writes.

Inside atomic, reads before the call see the preceding valid state and reads after
it see the final valid state. An error rolls back the group; catching a group failure
also requires the enclosing write transaction to roll back. Other operations keep
immediate constraint checks. This is not deferred uniqueness across a callback.
Cross-record business rules still belong in an explicit atomic callback.
