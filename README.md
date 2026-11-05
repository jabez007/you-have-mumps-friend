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
fingerprint. Read-only migration planning is available; execution is not implemented.
Adding an index changes the declared schema; existing data requires an explicit
migration and index rebuild.

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

## Inspect installed schemas

```python
class CatalogV2(CatalogProduct):
    __schema_version__ = 2
    note: str | None = None

next_db = Database(db.store, Registry(CatalogV2))
inspection = next_db.inspect_schema(CatalogV2)
assert inspection.status == "different"
assert inspection.installed.schema_version == 1
assert inspection.declared.schema_version == 2
assert db.get(CatalogProduct, "P1").sku == "SKU1"
```

Inspection reads the installed snapshot even when regular access with the new
declaration would raise `SchemaMismatch`. It runs in one transaction and does not
install, activate, or repair a schema. It never constructs model instances or
executes default factories.

| Status | Meaning |
| --- | --- |
| `uninstalled` | No installed schema or application data exists. |
| `unmanaged` | Application data exists without installed schema metadata. |
| `matching` | Installed and declared fingerprints agree. |
| `different` | Valid installed metadata differs from the declaration. |

`has_data` reports whether any node exists in the application global; it does
not count records. Incomplete metadata, invalid canonical JSON, a mismatched
global, or a bad snapshot fingerprint raises `StorageError`.

`SchemaSnapshot` exposes immutable canonical bytes and derived metadata. Its
normal representation excludes schema contents. `snapshot.to_dict()` explicitly
returns the full schema, including declared defaults, as a fresh dictionary.
Inspection does not validate records or index contents.

Compare snapshots independently of the database:

```python
from mccann_hub.yhmf import compare_schemas

changes = compare_schemas(inspection.installed, inspection.declared)
assert any(change.kind == "field_added" and change.path == ("note",) for change in changes)
```

Changes distinguish fields, storage types, validation, defaults, indexes,
relationships, and version metadata. Paths contain declared field names;
child keys are not part of these schema paths. Renames are reported as removals
and additions. Identity changes, destructive changes, unknown layouts, and
unsupported schema shapes require manual review. Comparison resolves local
Pydantic definitions but never fetches external JSON schemas.

Default values remain hidden in normal change representations. An added scalar
field with a literal default can describe a backfill. Required fields without
defaults and fields with default factories require manual values. Comparing
snapshots does not run validators or default factories.

## Review a migration plan

```python
plan = next_db.plan_migration(CatalogV2)
assert not plan.requires_manual_review
assert any(step.kind == "backfill_field" and step.path == ("note",) for step in plan.steps)
assert plan.steps[-1].kind == "activate_schema"
assert plan.to_dict()["execution_supported"] is False
assert plan.to_json() == next_db.plan_migration(CatalogV2).to_json()
```

Plans describe changes from the installed fingerprint to the current declaration.
They order backfills before index rebuilding and place schema activation after
validation and record metadata updates. Unique indexes add a uniqueness check;
reference changes add reverse-index rebuilding and validation. Matching schemas
produce an empty plan. Empty globals produce an installation plan; unmanaged data
requires manual review and is never adopted implicitly.

`to_json()` returns deterministic, compact ASCII JSON without timestamps. The
[plan fixture](tests/fixtures/add-field-migration-plan.json) defines the review
format. Normal representations and serialization exclude literal default values.
Use `to_dict(include_defaults=True)` or `to_json(include_defaults=True)` explicitly
to review them. Default payloads are JSON Schema representations, not encoded
native node values.

Nested backfills use declared field paths and apply only to existing owners.
They do not imply creating absent embedded objects or child records. Removing
fields, changing storage types, moving identities, retargeting references, unknown
formats, and unavailable default values require manual review. Existing default
changes do not request replacement of already stored values.

Plans are per top-level model. Identity and reference changes can require
coordination with other models. Custom Python validators and changes to factory
functions may not appear in JSON Schema; advance `__schema_version__` when that
logic changes and review it separately.

Planning performs schema inspection and a bounded data-presence check. It does
not scan or validate records, run factories or validators, rebuild indexes, or
write any database nodes. `requires_manual_review=False` does not certify data
compatibility. Execution will require a maintenance gate, fingerprint
revalidation, bounded checkpoints, and interruption/resume testing.

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
