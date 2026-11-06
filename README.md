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
keys; `save` replaces a record or inserts it. Detached saves use last-writer-wins
semantics: two readers can each change a copy and the later save replaces the
entire earlier result. Use `atomic` with the read inside the callback for safe
read/modify/write; reading before the callback does not gain this protection. `get` raises `NotFound` when absent,
and `delete` returns whether a record existed. Keys must match their declared type.

`db.atomic(callback)` passes a transaction handle to the callback. The handle
expires when the callback returns. Each restart creates a fresh handle and
reconstructs records from storage. All standalone operations also use transactions.
Nested `atomic` callbacks are rejected. Native mapper and value-validation
failures may also rerun the callback before being reported: the adapter revalidates
through YottaDB's final stable TP attempt. Persistent errors still roll back and
raise their original type. `RuntimeError` aborts and native control/error signals
pass through. See [ADR 0005](docs/adr/0005-native-validation-restarts.md).
If a backend write fails, the transaction must roll back even when the callback
catches the exception. Later operations on that handle raise `TransactionError`.

The registry rejects overlapping globals, aliases, unsupported field types,
and missing or ambiguous primary keys. Access checks both the installed schema
fingerprint and snapshot bytes inside each transaction, and incompatible declarations
raise `SchemaMismatch` before writes. New schemas use a framework-owned, versioned
contract; Python class names and presentation metadata do not change its fingerprint. Read-only migration planning and a bounded additive migration executor
are available.
Adding an index changes the declared schema; existing data requires an explicit
migration and index rebuild.

Automatic schema installation requires an empty application global, including
its root value and all descendants. Raw data at any key prevents installation;
use explicit adoption or an intentional external migration. Failed installation
and record writes roll back together.

For a small global already encoded exactly in YHMF layout one, explicitly call:

```python
adopted_count = db.adopt(Product)
```

Adoption requires absent installed-schema metadata, equality/unique namespaces,
and inbound-reference metadata. It accepts at most 100 records and 10,000 encoded
record nodes in one transaction. Each record must have either no `%m` metadata
or complete matching metadata. Fields, child counts, keys, constraints, unique
values, and reference targets are validated. Unknown nodes, partial metadata,
and arbitrary legacy encodings are rejected. Stored field bytes are preserved;
metadata and secondary structures are installed atomically. A failed adoption
leaves all data and metadata unchanged. Larger or differently encoded datasets
need an external, explicitly reviewed migration. Adoption requires a standalone
call outside `atomic`.

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
    name: str = "Main"
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
updates. Use the explicit mutations below for direct changes to existing owned paths.

Reads and writes reject collections larger than 1000 children and records larger
than 10000 stored nodes. These prototype bounds include presence markers and
record metadata. Native path limits also apply to deeply nested records.

## Patch scalar fields at an owned path

```python
from decimal import Decimal

db.patch(Warehouse, 7, changes={"name": "Renamed"})
db.patch(
    Warehouse, 7,
    at=("bins", "A01", "lots", "L-001"),
    changes={"quantity": Decimal("25.5")},
)
lot = db.get(Warehouse, 7, at=("bins", "A01", "lots", "L-001"))
```

Each path starts at the registered root key. Embedded objects take a field name;
keyed children take a collection field and a key of the child's declared type.
Missing owners raise `NotFound`. A patch changes scalar or `Reference` fields;
changing keys or replacing owned subtrees through `changes` raises `MutationError`.
Use `at` to patch inside an existing embedded object. Mutations never materialize
missing objects or collections implicitly.

A leaf patch validates the complete leaf model, including cross-field validators.
A scalar patch on an owner with descendants validates selected fields with their
constraints and model configuration. Custom validation on such an owner or any
ancestor makes partial mutation unsupported and raises `MutationError`. This
includes Annotated function validators, custom initialization, and post-init hooks.
Use complete `get`/`save` inside `atomic` for those models. No validator is silently
skipped to obtain a faster update.

Patches read their affected state within the transaction, write only changed
scalar nodes, and maintain only affected indexes and reference entries. They do
not rewrite sibling child collections. Different-field patches preserve both
assignments; same-field assignments serialize with the later commit winning.
For arithmetic, read the owned value inside the callback:

```python
def receive(tx):
    at = ("bins", "A01", "lots", "L-001")
    current = tx.get(Warehouse, 7, at=at)
    tx.patch(Warehouse, 7, at=at, changes={"quantity": current.quantity + Decimal("1")})

db.atomic(receive)
```

The callback may restart. Keep external side effects outside it. Failed partial mutations
make the transaction roll back even if the callback catches the exception.
See [ADR 0004](docs/adr/0004-hierarchical-mutations.md) for validation and storage
tradeoffs.

## Insert and delete keyed children

```python
db.insert_child(
    Warehouse, 7,
    collection=("bins", "A01", "lots"),
    record=Lot(number="L-002", quantity=Decimal("3")),
)
db.delete_child(Warehouse, 7, at=("bins", "A01", "lots", "L-002"))
```

Insertion requires an existing collection and derives the child key from its
validated identity. It rejects existing child data and validates the complete
new subtree. Deletion requires an existing keyed child, removes its subtree,
releases its outgoing references and unique claims, and preserves siblings.
Both update the collection count and enforce collection length constraints.
Persistence revalidates plain declared owned data, so unchecked `model_copy`
values cannot bypass constraints through `revalidate_instances="never"`. This
also hardens existing whole-aggregate saves.
They share the ancestor-validation restrictions of `patch`.

Child insertion counts declared aggregate nodes through ownership markers and
bounded key enumeration to preserve layout one's 10,000-node limit. Its size
check grows with the aggregate; scalar patches and child deletion avoid that
aggregate traversal. No cached size or revision metadata is added. Existing
v0.3 databases and older whole-aggregate writers retain the same layout.

Use `insert` to create a root and reject duplicates; `save` to intentionally
replace or upsert its whole aggregate; `patch` to change scalar/reference values;
and child operations to change keyed ownership. Wrap related operations in
`atomic` so they commit together. Parent or child deletion concurrent with a
nested update causes serialization and a missing-owner error on retry when the
delete wins. Unique-index conflicts allow one claimant. Failure and restart
preserve nodes, collection counts, indexes, and reference entries together.

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
| `matching` | Exact identity or a verified compatible legacy declaration. |
| `different` | Valid installed metadata differs from the declaration. |

`has_data` reports whether any node exists in the application global; it does
not count records. Incomplete metadata, invalid canonical JSON, a mismatched
global, or a bad snapshot fingerprint raises `StorageError`.
Declarations that generate nonfinite JSON numbers are rejected with `SchemaError`.

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

New installations use canonical schema format 2, generated from registered field
types, codecs, constraints, defaults, ownership, references, and indexes. Schema
format, application `__schema_version__`, storage layout, and codec version are
separate. Golden fixtures qualify the same bytes under Pydantic 2.12.0 and 2.14.3.

Legacy format-one metadata remains supported and unchanged. Opening an existing
database verifies its integrity and binds operations to its installed fingerprint.
Presentation changes and the specifically qualified incidental Decimal pattern
can compare compatible. Other unsupported or genuine changes fail safely. An
actual data migration can activate format 2 while using the existing bounded
executor; opening a database does not perform a migration or retag records.

Custom validators and default factories require application-managed revisions:
bump the owning model's `__schema_version__` whenever their persistence-relevant
behavior changes. Function bodies cannot be reliably fingerprinted. Built-in
constraints are recorded automatically; unknown validation metadata is rejected.
See [schema compatibility](docs/schema-compatibility.md) and
[ADR 0006](docs/adr/0006-canonical-schema-contracts.md) for guarantees and limits.

## Review a migration plan

```python
plan = next_db.plan_migration(CatalogV2)
assert not plan.requires_manual_review
assert any(step.kind == "backfill_field" and step.path == ("note",) for step in plan.steps)
assert plan.steps[-1].kind == "activate_schema"
assert plan.execution_supported
assert plan.to_json() == next_db.plan_migration(CatalogV2).to_json()
```

Plans describe changes from the installed fingerprint to the current declaration.
They order backfills before index rebuilding and place schema activation after
validation and record metadata updates. Unique indexes add a uniqueness check;
index removals produce `drop_index` steps, and uniqueness downgrades produce
`drop_uniqueness` steps. Reference changes add reverse-index rebuilding and validation. Matching schemas
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
compatibility. `execution_supported` identifies the executor's structural subset;
record validation still happens under maintenance before any backfill.

## Execute a scalar or index migration

Keep the exact source model declaration available alongside the target. The
executor accepts added scalar fields with literal defaults, new scalar indexes,
promotion or downgrade of existing equality indexes, and index removals. These operations
apply to root fields and fields inside existing embedded objects and children.
Existing field values, owned trees, references, and retained index nodes are
preserved. Index changes remain limited to scalar fields. New owned containers,
reference changes, field removals, and default factories remain unsupported.

```python
plan = next_db.plan_migration(CatalogV2)
# Review plan.changes, plan.steps, and any declared defaults before starting.
assert plan.execution_supported
status = next_db.begin_migration(CatalogProduct, CatalogV2, plan=plan)
migration_id = status.migration_id  # Retain this ID for recovery.
while status.phase != "complete":
    status = next_db.advance_migration(
        migration_id, CatalogProduct, CatalogV2, batch_size=25
    )
```

Each call commits at most one batch or phase transition. Batch sizes range from
1 to 100 records, with the existing per-record node and child limits. Four passes
validate source records and target values, backfill fields and indexes, verify
the results, then update record metadata. Index removals and uniqueness downgrades
also enter a bounded `cleanup` phase before activation. A final transaction publishes the
target snapshot and fingerprint, marks the job complete, and releases maintenance.
An empty page advances the phase; the cursor resets for the next pass.

Adding `unique=True` to an existing scalar field uses the same methods.
Validation reserves every new non-null unique value under the migration job,
using the same normalization as ordinary writes. Duplicates across batches raise
`UniqueViolation` before application indexes or record metadata change. The failed
batch and its reservations roll back together. You can then abort and correct
the source data before reviewing and starting a new plan. Multiple null values
remain allowed. Duplicate errors omit indexed values and record identities.

Backfill builds new equality entries and unique claims from those validated
reservations. Promoting an equality index adds claims without deleting or
rewriting its existing entries. Verification checks both the reservations and
the active entries and claims before activation. Reservations contain normalized
values and owner identities inside framework metadata. They remain with completed
and aborted jobs, use space proportional to the new non-null unique values, and
are absent from normal migration status output.

Changing `unique=True` to `index=True` removes unique claims while retaining
equality entries and queries. Removing the index declaration removes equality
entries and any unique claims while preserving stored field values. After
activation, duplicates are allowed for a downgraded field. Equality queries on a
field whose index was removed raise `QueryError`; they never imply a scan.

The update batches remove expected entries and claims by parent. Verification
checks that retained nodes still match and removed nodes are absent. Cleanup
then drains retired namespaces, including orphan nodes, before the target schema
can activate. Each cleanup call deletes at most `batch_size` leaf nodes, with
the usual 1 through 100 limit. During cleanup, the record counters stay unchanged;
committed deletions and an internal deletion count record progress. Repeated
calls may return the same status until cleanup finishes. Raw writers must remain
stopped. If a retired namespace reappears before activation, the job returns to
cleanup with maintenance still held.

Nested changes use dotted declaration paths such as `lots.note` and
`lots.samples.reading`. Scalar defaults are added to every existing owner at that
path. Optional embedded objects and nullable collections that are null remain
null, and empty collections stay empty. The executor does not create children or
embedded objects to apply a default. A default factory is rejected even when no
owner currently exists.

Each parent record and its complete owned tree remain one transaction unit.
Batch limits and progress counters count parents, with the existing per-parent
node and child limits also enforced against the target tree before backfill.
Adding a field that would exceed those limits fails validation without changing
application data. Owned schema version increases are supported alongside
additive changes; decreases require manual review, as root version decreases do.

Nested equality queries still return owning root records. After adding an
indexed `note` to `Lot` and updating `Warehouse.lots` to use that declaration,
`db.query(WarehouseV2).where(**{"lots.note": None}).all()` selects warehouses
with matching lots. A nested unique constraint covers all occurrences at its
declaration path, including different children under the same parent. The
preflight checks those duplicates as well as duplicates across parent batches.

The database-wide gate blocks ordinary operations from current mapper clients.
Schema inspection, planning, and `migration_status(migration_id)` remain
available. Inspection reports the source snapshot and active migration ID until
activation. The fingerprint barrier makes older clients reject the migrating
model. Stop older clients accessing other models and all raw YottaDB writers
before starting. Validators must be deterministic and free of external side
effects because native transactions can retry them.

To resume after interruption, reconstruct `Database` with the target registry,
read `migration_status(migration_id)`, and continue advancing with the same
source and target declarations. The executor verifies both saved snapshots on
every active call. Data writes and their checkpoint commit together. A failed
batch rolls back and leaves the gate held at its preceding checkpoint; it does
not release partially migrated data to callers. Normal status output omits
record keys; `status.to_dict(include_cursor=True)` explicitly exposes the encoded
cursor.

`abort_migration(migration_id)` restores source access before any update batch or
cleanup deletion commits, including after a validation failure. Once updates
commit, abort is refused: recovery requires resume or operator-managed restoration from backup.
This also applies to cleanup of orphan metadata in an empty application global.
Do not remove the gate or fingerprint barrier manually. Migration lifecycle
methods require standalone calls outside `db.atomic`; each manages its own
transaction. Completed and aborted job metadata remains available for inspection.

## Rebuild existing indexes

An explicit rebuild repairs equality entries and uniqueness claims from stored
records while keeping the installed model declaration unchanged. It supports
declared root and nested indexes, including reference equality indexes. Existing
reverse edges and target metadata receive the same maintenance checks;
reverse-reference counts are left unchanged.

```python
plan = db.plan_index_rebuild(CatalogProduct)  # All declared indexes by default.
print(plan.to_json())
assert plan.execution_supported
status = db.begin_index_rebuild(CatalogProduct, plan=plan)
job_id = status.migration_id
while status.phase != "complete":
    status = db.advance_index_rebuild(job_id, CatalogProduct, batch_size=25)
```

Use `fields=("sku",)` to select particular indexes, or dotted names such as
`fields=("lots.serial", "lots.samples.reading")` for nested indexes. Selections
must name distinct declared indexes. The review plan is deterministic and reads
only schema metadata. It exposes no indexed values. A model with no indexes or
a declaration that differs from the installed schema has an unsupported plan.

Rebuilds use the same maintenance gate, schema barrier, `migration_status`, and
`abort_migration` methods as schema migrations. The phases are `validate`,
`clear_indexes`, `backfill`, `verify`, `activate`, and `complete`. Validation reads
authoritative records and reserves selected unique values before any index is
cleared. Conflicting values raise `UniqueViolation`; rebuilds never change field
values or choose a surviving record. Missing or invalid record fields fail
validation and are not filled from defaults.

Clearing removes at most `batch_size` leaves per call from the selected equality
and claim namespaces, including stale and orphan nodes. Backfill reconstructs
selected entries in bounded parent pages, and verification checks rebuilt entries
and claims. Unselected namespaces, record bytes, record metadata, and the schema
snapshot and fingerprint are preserved. The `retagged` counter stays zero;
`validated`, `backfilled`, and `verified` count parent records.

Retain the job ID for recovery. Reconstruct a database with the same registry
and resume with `advance_index_rebuild(job_id, model)`. The selection is persisted
with a digest and cannot be changed during resume. Abort is available until the
first committed clearing deletion or reconstruction batch. After that, recovery
requires resume or operator-managed restoration. Raw writers must remain stopped.
Rebuilding reverse-reference counts, repairing application records, and pruning
historical maintenance metadata remain outside this operation.

## v0.4 release scope

Version `0.4.0` adds scalar/reference patches, owned-path reads, keyed child
insertion and deletion, and atomic composition. It preserves layout one,
whole-aggregate `save`, schema migrations, and existing index/reference identities.
Unsafe ancestor validation is explicitly rejected. Child insertion retains a
bounded aggregate size check; optimistic revisions, automatic change tracking,
and Go bindings remain deferred. Canonical schema format 2 is implemented in v0.5.

Native qualification includes deterministic process races, forced restarts,
rollback, index/reference checks, legacy migration compatibility, and the installed
wheel workflow. [Measured native performance](docs/performance.md) shows constant
patch node work as unrelated lot counts grow, with material framework overhead
relative to direct native assignment.

## v0.3.1 hardening

Version `0.3.1` rejects implicit installation over any unmanaged global data,
checks both installed metadata nodes during CRUD, and adds bounded explicit
layout-one adoption. Schema identities and layout version one are preserved.
Legacy fingerprint drift across Pydantic versions is confirmed and documented;
a new fingerprint format is proposed, not activated. Detached `save` remains
last-writer-wins; `atomic` protects read/modify/write when the read occurs inside
the callback. Optimistic record revisions are deferred beyond v0.4.

## v0.3 release scope

Version `0.3.0` completes the schema-evolution milestone within the scope below.

The v0.3 scope is schema inspection and comparison, deterministic review plans,
scalar-default additions in existing owned trees, scalar index additions,
promotions, downgrades and removals, and explicit declared-index rebuilds.
Maintenance jobs provide bounded updates, atomic activation, safe early abort,
and interruption/resume. Storage layout version 1 and legacy format-one schema
jobs remain supported. New owned containers, field deletion or relocation,
storage-type changes, reference migrations, and default-factory backfills remain
later work.

Qualification covers the deterministic checks, installed-wheel schema migration
and index reconstruction workflow, and native
rollback, restart, process-death, and concurrency suites. The native CI matrix
uses Python 3.12 with YottaDB r2.02 on Ubuntu 22.04 and r2.06 on Ubuntu 24.04.
These results apply to those tested environments, not every accepted runtime or
dependency version.

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

## Benchmark native mutations

```sh
YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
```

After tests, this runs `scripts/benchmark-mutations.py` in the same disposable
native database. Both native CI environments run it too. Serial measurements
report median/p95 latency and adapter operation counts at 1, 10, 100, and 1,000
lots, with five warmups. The unindexed case compares direct node assignment,
deep patch, and read/modify/save. Indexed quantity, unique serial, and indexed
reference changes compare patch and replacement with the same integrity requirements.

Version-two output also includes four-process contention bursts and separate
cProfile summaries. Contention compares independent roots, independent children,
and a shared child; callback-attempt counts expose retry work. Final values,
indexes, and reverse-reference counts are checked after each workload. Profiles
run without the operation-counting wrapper and are separate from latency samples.

Direct assignment measures a `NativeStore` transaction and scalar write. Latency
measurements include Python and counting overhead. Serial measurements use a warm,
uncontended database; contention throughput includes controller coordination.
These describe the measured workloads and do not establish production throughput.
See the [performance report](docs/performance.md) for native evidence and limits.
Offline operation counts establish work scope only.

## Run native storage tests

See [correctness testing](docs/testing.md) for generated state machines, independent
integrity assertions, fault injection, longer profiles, and reproduction commands.

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
