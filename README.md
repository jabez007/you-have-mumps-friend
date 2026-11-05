# You have MUMPS, friend

YHMF maps explicit Python model operations to inspectable YottaDB globals.
The distribution is `mccann-hub-yhmf`, imported as `mccann_hub.yhmf`.
Python 3.12 or later is required. This is an early prototype under GPL-3.0-or-later.

The [architecture](docs/yottadb_hierarchical_mapper_architecture.md) describes
the intended milestones. The prototype supplies a versioned storage contract,
declarative models, embedded objects, keyed children, enforced references,
explicit CRUD, and bounded indexed queries through native and in-memory adapters.

## Persist a model

```python
from decimal import Decimal
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry

class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)

db = Database(MemoryStore(), Registry(Product))
db.insert(Product(id="P42", quantity=Decimal("12.50")))
product = db.get(Product, "P42")
product.quantity -= Decimal("2")
db.save(product)
assert db.get(Product, "P42").quantity == Decimal("10.50")
```

Use `NativeStore()` with a configured YottaDB environment for durable storage.
`MemoryStore()` lasts only as long as the Python process. `insert` rejects existing
keys; `save` replaces a record or inserts it. `get` raises `NotFound` when absent,
and `delete` returns whether a record existed. Keys must match their declared type.

`db.atomic(callback)` passes a transaction handle to the callback. The handle
expires when the callback returns. Each restart creates a fresh handle and
reconstructs records from storage. All standalone operations also use transactions.
Nested `atomic` callbacks are rejected.
If a backend write fails, the transaction must roll back even when the callback
catches the exception. Later operations on that handle raise `TransactionError`.

The registry rejects overlapping globals, aliases, unsupported field types,
and missing or ambiguous primary keys. Access checks an installed schema
fingerprint, and incompatible declarations raise `SchemaMismatch` before writes.
Changing a model name, field declaration, default, or constraint can change the
fingerprint. There is no migration engine yet. Adding an index changes the
declared schema; existing data requires an explicit migration and index rebuild.

## Store owned child records

```python
from mccann_hub.yhmf import ChildField, Children

class Lot(Model):
    number: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)

class Bin(Model):
    code: str = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="number")

class Warehouse(Model):
    __global__ = "^Warehouse"
    id: int = Field(primary_key=True)
    bins: Children[Bin] = ChildField(key="code")

db = Database(MemoryStore(), Registry(Warehouse))
db.insert(Warehouse(id=7, bins={
    "A01": Bin(code="A01", lots={"L-001": Lot(number="L-001")}),
}))
assert db.get(Warehouse, 7).bins["A01"].lots["L-001"].quantity == Decimal("0")
```

Declare an embedded object with a field whose type is another `Model`. Owned
models have no `__global__`; their parent controls persistence and deletion.
`Children` is a dictionary, and each key must match the child's primary key.
Changing the parent or dictionary key changes the child's physical identity.
Edit the loaded aggregate and save its root inside `atomic` when coordinating
updates. Direct child CRUD is not implemented yet.

Reads and writes reject collections larger than 1000 children and records larger
than 10000 stored nodes. These prototype bounds include presence markers and
record metadata. Native path limits also apply to deeply nested records.

## Link independently persisted records

```python
from mccann_hub.yhmf import Reference
from mccann_hub.yhmf.errors import ReferenceViolation

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

Every reference target must be a registered top-level model. Non-null keys must
match the target's primary-key type and resolve when the source is written.
References also work inside embedded objects and owned children. An optional
`Reference[Product] | None` permits `None`; once set, it still restricts deletion.
Changing or removing a reference releases the previous target.

The default deletion policy is `restrict`, even for optional references. It
cannot be disabled. `set_null` and cross-record `cascade` raise
`NotImplementedError`. Owned-child deletion continues to follow the parent.

Create targets before sources inside `atomic`. Self-references are supported.
For cycles across new records, create nullable links first and then connect
them in the same transaction. Constraints apply after each mapper write.
Reference identities persist as keys and do not resolve targets automatically
when a source is read. Read a target with `db.get(Product, source.product.key)`.

Reverse entries and inbound counts change in the same native transaction as
the record. Raw native writes can bypass these constraints, so applications
must preserve the [storage contract](docs/storage-format.md) when writing globals
outside the mapper. There is no automatic repair of damaged reverse entries.

## Declare equality and unique indexes

```python
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

`unique=True` also creates an equality index. Multiple `None` values are allowed
in a unique field, and their equality entries remain queryable. Strings use exact,
case-sensitive comparison. Numerically equal decimals and floating-point zero
share index values; stored decimal precision remains unchanged.

Index scalar or reference fields, including those inside embedded objects and
owned children. Indexing an entire container is rejected. A nested unique field
is unique across all occurrences at that declaration path under the registered
top-level model, including children of different parents.

Every insert, replacement, and deletion updates its indexes and uniqueness claims
in the record's transaction. Constraints apply after each write. Changing or
deleting an indexed value releases its old claim. Oversized native index keys
raise errors before record writes; values are never truncated or hashed to fit.

## Query bounded results

```python
query = db.query(CatalogProduct).where(price=Decimal("12.5"))
assert query.explain().strategy == "equality_index"
assert [product.id for product in query.all(limit=25)] == ["P1"]
assert query.all(limit=25, after="P1") == []
assert db.query(CatalogProduct).where(id="P1").explain().strategy == "direct_key"
assert db.check_indexes(CatalogProduct, limit=25).issues == ()
```

Queries accept one equality predicate on a declared index or the primary key.
An unindexed or unknown field raises `QueryError`. There is no fallback scan.
Filter values must satisfy the declared field type and constraints.

`all` defaults to 100 root records and permits limits from 1 through 1000.
Results use native primary-key order. Pass the last returned key as `after` to
continue. Each page uses its own transaction; pages do not share a snapshot.
`db.scan(ModelType, limit=25, after=last_key)` explicitly authorizes a bounded
page of root records when enumeration is needed.

For indexes inside owned trees, use dotted declaration paths such as
`lines.product` as keyword names through a dictionary passed to `where`.
Such queries return each matching owning root once, even when several children
match. Queries over standalone child identities remain a later extension.

`explain` reports a `direct_key` or `equality_index` plan without filter values.
`check_indexes` checks expected secondary entries and unique claims for a bounded
page of records. Its issues contain field names and error kinds, without indexed
values. A full page returns `next_key`; the next page may be empty. This audit
does not search all index entries for orphans or repair data. Indexed queries
reject encountered orphan entries and entries whose values disagree with records.

## Run development checks

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
```

Ruff checks cyclomatic complexity with a maximum of 10. Native tests skip
unless you explicitly select a disposable database. The in-memory adapter does
not prove native conflict handling or process concurrency.

## Run native storage tests

Install [YottaDB](https://docs.yottadb.com/AdminOpsGuide/installydb.html) and
set `ydb_dist` to its installation directory. Then run:

```sh
uv sync --extra native
bash scripts/test-native.sh -q
```

The script creates and removes a dedicated temporary database. Native CI runs
this suite with Python 3.12 on YottaDB r2.02 with Ubuntu 22.04 and r2.06 with
Ubuntu 24.04. An unpacked binary
archive alone is insufficient because YottaDB requires a correctly installed
`gtmsecshr` helper. Setting `YHMF_TEST_NATIVE=1` manually opts into tests against
your current database; use only disposable data.

Native qualification covers forced transaction restarts, rollback of schema
installation, and four independent processes committing 160 updates to one
record. Concurrent reads assert that both fields agree throughout those updates.
Reference race tests pause independent processes after reading the same incoming
count, then release them together. They check reference creation against target
deletion, concurrent addition and removal, and index state after native restarts.
Unique-claim race tests force two processes to read the same unclaimed value,
then verify that exactly one source and its reverse-reference updates commit.
These tests establish correctness for the tested environment, not throughput
or compatibility with other engine releases.

YDBPython uses process-based concurrency. Callbacks passed to `atomic()` can
execute repeatedly after transaction conflicts, so keep external side effects
outside the callback.

YottaDB and YDBPython have separate upstream licenses. This repository does
not provide legal advice about applications that combine them with this code.
