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

An optional `Reference[Product] | None` permits `None`. Once set, it still restricts
deletion. Changing or removing a reference releases the previous target.

## Read the target

Reading a source does not load its target. Resolve it explicitly:

```python
lot = db.get(StockLot, "L-001")
assert db.resolve(lot.product).id == "P42"
assert db.atomic(lambda tx: tx.resolve(tx.get(StockLot, "L-001").product)).id == "P42"
assert [product.id for product in db.resolve_many([lot.product, lot.product])] == ["P42", "P42"]
assert [product.id for product in db.get_many(Product, ["P42", "P42"])] == ["P42", "P42"]
```

`resolve(Reference[T])` returns `T`. `resolve_many` and `get_many` accept finite sequences
of at most 1000 items, preserve order and duplicate positions, and read each distinct
target once per call. A batch is one transaction; a native restart rebuilds its cache.
For a shared snapshot with source reads or later writes, call these methods inside
`atomic`. There is no cache across calls and no implicit loading on attribute access.

Results are detached models. Duplicate positions share the fetched Python instance
within that result, so copy it explicitly if independent mutable values are needed.
Empty batches are permitted. Missing targets raise `NotFound` without exposing keys;
an unregistered target or mismatched key type raises `SchemaError`.

## Understand deletion

The only deletion policy is `restrict`, and it cannot be disabled, even for optional
references. `set_null` and cross-record `cascade` raise `NotImplementedError`. Owned
children still follow their parent.

## Create records that link to each other

Create targets before sources inside [`atomic`](transactions.md). Constraints apply
after each mapper write. Self-references work. For cycles across new records, create
nullable links first, then connect them in the same transaction.

## Write around the mapper with care

Reverse entries and inbound counts change in the same native transaction as the record.
Raw native writes can bypass these constraints. If you write globals outside the mapper,
you must preserve the [storage contract](../storage-format.md). YHMF does not repair
damaged reverse entries.
