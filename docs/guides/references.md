# Link independent records

Use a `Reference` when two records have separate lifecycles. The source stores the
target's key, and YHMF checks that the target exists:

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)


class StockLot(Model):
    __global__ = "^StockLot"
    number: str = Field(primary_key=True)
    product: Reference[Product] = Field(on_delete="restrict")


db = Database(MemoryStore(), Registry(Product, StockLot))
db.insert(Product(id="P42"))
db.insert(StockLot(number="L-001", product=Reference[Product](key="P42")))
try:
    db.delete(Product, "P42")
except ReferenceViolation:
    pass
assert db.get(Product, "P42").id == "P42"
```

## Declare a reference

Every target must be a registered top-level model. A non-null key must match the
target's primary-key type and must resolve when the source is written. References also
work inside embedded objects and owned children.

An optional `Reference[Product] | None` permits `None`. The default policy still
restricts deletion. Changing or removing a reference releases the previous target.

For static key checking, infer a constructor from the registered primary key:

```python
product_reference = db.registry.reference(Product, lambda p: p.id)
reference = product_reference("P42")
assert type(reference) is Reference[Product]
```

Strict mypy infers ReferenceFactory[Product, str] and rejects product_reference(42).
Select the primary key itself, not an expression or another scalar. The factory
checks the exact declared key type and encoding at runtime, including rejecting
bool for an integer key. It does not load the target, normalize keys, or prove that
the record exists. Persistence and resolve perform their usual reference checks.
Keep selectors pure; they run on declarations without record reads.

The result is the existing Reference[Product], so generic arity, schema fingerprints,
and stored bytes stay unchanged. Existing Reference[Product](key=...) remains valid
and keeps its runtime key checks. Standard typing cannot derive a different key
attribute type from arbitrary Reference[T]; use the inferred factory where static
constructor checks matter.

## Read the target

Reading a source does not load its target. Resolve it explicitly:

```python
lot = db.get(StockLot, "L-001")
assert db.resolve(lot.product).id == "P42"
assert db.atomic(lambda tx: tx.resolve(tx.get(StockLot, "L-001").product)).id == "P42"
assert [product.id for product in db.resolve_many([lot.product, lot.product])] == ["P42", "P42"]
assert [product.id for product in db.get_many(Product, ["P42", "P42"])] == ["P42", "P42"]
```

`resolve(Reference[T])` returns `T`. `resolve_many` and `get_many` accept finite
sequences of at most 1000 items, preserve order and duplicate positions, and read each
distinct target once per call. A batch is one transaction; a native restart rebuilds its
cache. For a shared snapshot with source reads or later writes, call these methods
inside `atomic`. There is no cache across calls and no implicit loading on attribute
access.

Results are detached models. Duplicate positions share the fetched Python instance
within that result, so copy it explicitly if independent mutable values are needed.
Empty batches are permitted. Missing targets raise `NotFound` without exposing keys;
an unregistered target or mismatched key type raises `SchemaError`.

## Read incoming references

`referrers` reads a bounded, read-only inverse relation directly from maintained reverse
entries. Supply the registered source model to select its native prefix:

```python
incoming = db.referrers(lot.product, source=StockLot, limit=25)
assert [hit.root_key for hit in incoming] == ["L-001"]
assert incoming[0].at == () and incoming[0].field == "product"
assert db.referrers(lot.product, source=StockLot, after=incoming[-1].cursor) == []
```

Each `Occurrence` contains the complete model owning that exact reference. Owned links
return their child or embedded owner, with its root key and concrete `at` path. Several
fields in one record produce distinct occurrences. The target's existence and schema are
checked without loading its aggregate. Each source owner is validated, and the exact
field and reverse identity must agree; another valid reference cannot hide a stale edge.

Order is native source-root key order, then location digest. Limits are 1 through 1000,
default 100. Cursors bind to the source schema and target identity. They use the same
[paging rules](indexes-and-queries.md#query-inside-owned-trees) as equality occurrences.
The interface checks encountered entries and the count marker's basic consistency;
it does not audit missing reverse entries by scanning every source record.

For many-to-many relationships, store keyed owned links containing References. Removing
a link releases its reference; the independent target keeps its lifecycle. The inventory
example demonstrates
[products and suppliers](../../examples/inventory/README.md#products-and-suppliers).

## Understand deletion

Declare the policy on the incoming Reference field:

| Policy | When its target is deleted |
| --- | --- |
| restrict, the default | Reject deletion if the source survives the complete cascade. |
| set_null | Validate and clear this nullable field on its surviving owner. |
| cascade | Delete the source's nearest keyed owner and its owned subtree. |

```python
class OptionalLot(Model):
    __global__ = "^OptionalLot"
    id: str = Field(primary_key=True)
    product: Reference[Product] | None = Field(default=None, on_delete="set_null", index=True)


optional = Database(MemoryStore(), Registry(Product, OptionalLot))
optional.insert(Product(id="P"))
optional.insert(OptionalLot(id="L", product=Reference[Product](key="P")))
assert optional.delete(Product, "P")
assert optional.get(OptionalLot, "L").product is None
assert optional.check_indexes(OptionalLot).issues == ()
```

For a reference inside an embedded value, cascade removes its nearest keyed child
or independent root. It does not automatically remove the whole root when the
reference belongs to a keyed child. Ownership still removes every descendant of a
deleted owner. A parent deletion dominates overlapping child deletions. Cycles are
discovered before writes; internal restrict edges do not block deleting the complete
closure. External restrict edges reject the entire operation.

The transaction validates all encountered sources, combines null changes per owner,
checks final collection lengths and root budgets, and updates normal indexes,
unique claims, reverse edges, counts, and values together. Existing partial-validation
restrictions apply: unknown dependencies in custom ancestor validators can require
an explicit application workflow. Null-rejecting validators reject the deletion;
validators cannot retain a dangling reference. Unrelated children and indexes survive.

Register every incoming source declaration, including those in other independent
globals. Without its Python declaration, the mapper cannot run source validators or
interpret policy safely. Each delete permits 100 distinct decoded owners, 1000
incoming edges across the closure, and 10,000 distinct decoded value nodes. Bounds
raise MutationError before writes; unlink sources in separate explicit transactions
before retrying a larger deletion. There is no partial progress or background job.
The target itself counts toward these limits. Native restarts rebuild the plan and
its cache; ordinary atomic callback retry guidance still applies.

Policy changes alter the source contract. Review plan_migration and run the explicit
validation/retag migration before using the new declaration. Records and reverse
edge bytes stay unchanged. Older clients conservatively reject the new source
identity or continue restricting target deletion. See
[ADR 0011](../adr/0011-bounded-reference-deletion.md).

## Create records that link to each other

Create targets before sources inside [`atomic`](transactions.md). Constraints apply
after each mapper write. Self-references work. For cycles across new records, create
nullable links first, then connect them in the same transaction.

## Write around the mapper with care

Reverse entries and inbound counts change in the same native transaction as the record.
Raw native writes can bypass these constraints. If you write globals outside the mapper,
you must preserve the [storage contract](../storage-format.md). YHMF does not repair
damaged reverse entries.
