# Read and write the data from M

YHMF stores data as ordinary globals, so M routines can reach it without Python. Reads
work directly. For writes, export the schema-bound routine below; raw writes bypass
validation and bookkeeping.

The outputs below come from a live YottaDB r2.06 database that holds the warehouse from
[get started](../getting-started.md):

```text
^Warehouse(7,"%m","fingerprint")="a83a80ea6c524658e44d6321021fe3f77d795996f8786a0bba26fb2ecbc1851a"
^Warehouse(7,"%m","layout")=1
^Warehouse(7,"%m","schema")=1
^Warehouse(7,"%m","type")="Warehouse"
^Warehouse(7,"bins")="c:1"
^Warehouse(7,"bins","s:A01")="o:1"
^Warehouse(7,"bins","s:A01","code")="s:A01"
^Warehouse(7,"bins","s:A01","lots")="c:1"
^Warehouse(7,"bins","s:A01","lots","s:L-001")="o:1"
^Warehouse(7,"bins","s:A01","lots","s:L-001","number")="s:L-001"
^Warehouse(7,"bins","s:A01","lots","s:L-001","quantity")="d:12.50"
^Warehouse(7,"id")="i:7"
^Warehouse(7,"name")="s:Main"
```

## Read the stored form

The [storage contract](../storage-format.md) defines every encoding. These rules cover most
reads:

- Integer keys are plain subscripts. String keys start with `s:`.
- Values start with a type tag such as `s:`, `i:`, `d:`, `f:`, `b:`, `x:`, or `n:`.
- `o:1` marks an embedded object or child record.
- `c:N` is the number of children in a collection.
- `%m` holds record metadata. Its values have no type tags.

## Read values in M

Strip the tag before you use a value. To list the lots in a bin:

```m
 set lot=""
 for  set lot=$order(^Warehouse(7,"bins","s:A01","lots",lot)) quit:lot=""  do
 . write $extract(lot,3,99)," ",$extract(^Warehouse(7,"bins","s:A01","lots",lot,"quantity"),3,99),!
```

This prints `L-001 12.50`.

> **Warning:** M arithmetic on a tagged value gives a wrong answer with no error.
> `"d:12.50"+1` is `1`, because M reads the leading text as zero. Strip the tag first:
> `$extract(value,3,99)+1` is `13.5`.

The sample [YHMFREAD.m](../../examples/m/YHMFREAD.m) provides
$$payload^YHMFREAD(raw,expected_tag) and $$isnull^YHMFREAD(raw). Put the routine on
the application's M routine path and specify the tag from its schema:

```m
 set raw=^Warehouse(7,"bins","s:A01","lots","s:L-001","quantity")
 write $$payload^YHMFREAD(raw,"d"),!
```

The helper checks the expected prefix and returns its encoded payload. It does not
validate canonical numeric syntax, constraints, schema metadata, or index integrity.
Binary payloads remain base64, dates remain ISO text, and Decimal/large-integer
payloads retain their exact decimal text. Applying M arithmetic imposes the engine's
numeric precision/range. Distinguish null with isnull; the empty payload of n: is
not an empty stored string. Native tests run this reader against committed goldens.

The [native observations](../benchmarks/native-value-semantics-2026-11-06.json) cover
both supported engines and journaling modes. They show the same precision loss when
M performs arithmetic on long untagged numeric text, while text payload reads retain
all digits. [ADR 0012](../adr/0012-retain-tagged-record-values.md) keeps record tags
and treats ordered indexes as a separate contract. These are semantic observations;
no timing was measured.

## Export an explicit M writer

```python
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry


class Part(Model):
    __global__ = "^Part"
    id: str = Field(primary_key=True, min_length=1)
    sku: str = Field(unique=True, min_length=1)
    quantity: int = Field(ge=0, le=999)


parts = Database(MemoryStore(), Registry(Part))
source = parts.m_routine(Part, name="PartAPI")
assert source.startswith("PartAPI ; SPDX-License-Identifier")
# Write source to PartAPI.m on your application's routine path.
```

Exporting source reads and verifies current schema metadata without changing it.
When a routine name matches a generated label, the source uses a private initial
label. The filename and callable entry points retain the requested routine name.
If a supported legacy snapshot is installed, the source binds to that exact legacy
identity. No metadata conversion occurs. If metadata is absent, the first valid M
insert installs it only if the application global and associated namespaces are
empty, in the same transaction as the record and indexes. Other unmanaged globals
require explicit adoption. Regenerate routines after activating a changed schema.

From plain M, supply every field as an untagged, named scalar:

```m
 new input,status
 set input("id")="P1",input("sku")="SKU1",input("quantity")=25
 set status=$$insert^PartAPI(.input)
 ; status="ok"; Python get() and indexed queries now see the record.
 set input("quantity")=30
 set status=$$replace^PartAPI(.input)
 set status=$$delete^PartAPI("P1")
```

The generator supports flat non-null scalar and Reference models with ordinary equality
and unique indexes. It enforces string length and numeric gt/ge/lt/le bounds, with
ASCII string values and canonical signed integers of at most 18 digits. Strings may
contain ASCII controls; NUL is forbidden in keys. Python retains its full Unicode
and scalar integer domains. An existing record outside the M stored-value domain
returns unsupported without changes. Use Python for those records.

Export rejects custom Python validators, unsupported constraints or normalization,
nullable values, embedded objects, scalar lists, choices, ordered
indexes, and more than 16 fields per owner. Keyed trees have the separate operations
below. The generator does not silently omit rules. Every inserted flat/leaf field
must be supplied even if Python declares a default; defaults/factories are not run.
M does not distinguish Python runtime types, so the declaration determines how each
canonical textual input is encoded. This is validation of the declared M subset,
not execution of Pydantic in M.

The insert/replace/delete entry points each own one native transaction and reject
a caller with nonzero tlevel.
All live schema/record/index checks and derived reads repeat after a restart. Changes
to data, equality entries, and unique claims commit or roll back together. Existing
records and expected claim ownership are checked before replacement/deletion. A
failed uniqueness claim cannot leave released old claims. Delete conservatively
refuses active inbound references, including sources with cascade/set_null policies;
Python handles those policies. Invalid count/edge metadata fails closed. A valid
zero-count namespace is removed when its target is deleted. Newly exported routines validate the live
maintenance checkpoint and its schema barrier in the write transaction. They block
the affected model while unrelated writes can proceed. Corrupt or unknown checkpoint
formats fail closed. Existing exported routines retain their conservative global
gate check until regenerated; their installed schema bindings do not change.

The transaction-owning entry points return ok, exists, missing, invalid, invalid_key,
key_size, schema,
unmanaged, maintenance, corrupt, unsupported, unique, references, or nested. Their failure
statuses leave the call's database state unchanged. Unexpected engine errors roll back the
owned transaction and propagate to the caller's error handler. They are not
reported as success or silently converted into a validation failure.

unsupported means an existing value has a recognized storage shape but M cannot
validate or manipulate it within its qualified domain. It does not certify that
Python would accept the record. For example, an existing Unicode string, an integer
over 18 digits, or a Decimal outside the fixed-unit domain is refused without writes.
Invalid tags, markers, identities, malformed scalar text, and enforceable constraint
violations still return corrupt.

Stored Decimal precision checks remove trailing zeros and treat every signed or
scaled zero as zero. Python-written 1.5000000, 0E-7, and 0E+18 are therefore readable
when their normalized values satisfy the declaration and exact unit limits. Patches
to other fields preserve those original payload bytes. Incoming M inputs keep the
raw precision restrictions described above. Regenerate existing routines for these
diagnostics and stored-value checks; installed data needs no conversion.

The [ADR](../adr/0013-explicit-m-writer-contract.md) records the trigger comparison
and initial contract. No trigger is installed by export or generated CRUD. Do not
combine these writers with external triggers that mutate managed data/indexes.
Raw writes remain outside the managed guarantees and are not an authorization path.

## Mutate a keyed hierarchy

Warehouse exports a routine for leaf children under an already managed hierarchy:

```python
from examples.inventory import MODELS, Warehouse

inventory = Database(MemoryStore(), Registry(*MODELS))
warehouse_source = inventory.m_routine(Warehouse, name="WarehouseAPI")
assert "insertchild(rootkey,path,input)" in warehouse_source
```

Create the root, locations, bins, products, and lots through Python first. Paths are
local M arrays alternating declared collection names and plain child keys. Insert
addresses a collection; patch/delete address a child. Supply complete inserted leaf
fields, or only changed scalar/reference fields for a patch:

```mumps
 new path,input,status
 set path(1)="locations",path(2)="DOCK",path(3)="bins",path(4)="B1",path(5)="balances"
 set input("id")="LOT-001",input("lot")="LOT-001",input("product")="WIDGET",input("quantity")="2.50"
 set status=$$insertchild^WarehouseAPI("MAIN",.path,.input)
 if status'="ok" quit
 set path(6)="LOT-001"
 kill input
 set input("quantity")="3.75"
 set status=$$patchchild^WarehouseAPI("MAIN",.path,.input)
 if status'="ok" quit
 set status=$$deletechild^WarehouseAPI("MAIN",.path)
```

These calls check live root schema/metadata, ancestor identities and markers, then
operate on that leaf and its affected bookkeeping. Insertion bounds collection
counts and root nodes using fixed-width c:N counts; only variable-width structural
branches require traversal. Patches do not load sibling quantities. Index names,
concrete locations, reverse entries/counts, and every affected stored ancestor sum
use the existing Python storage identities in one TP. Unchanged references are not
resolved during scalar patches. A numerically unchanged quantity leaves total/index
nodes untouched. Decimal wire limits, including raw precision constraints, also apply
to generated sum values; exact fixed-unit arithmetic never rounds to M's numeric precision.

insertchildtx, patchchildtx, and deletechildtx participate in an existing caller TP.
Success leaves it open; any failure aborts all its levels, including earlier flat or
child participants. Read dependent business values inside the caller's restart point.
Data-dependent failures are revalidated on the native stable attempt before the
status is returned. A participant may restart the caller's entire TP first. Reset
mutable paths/input arrays changed during the attempt as well as re-reading database
values. See [ADR 0005](../adr/0005-native-validation-restarts.md).
Each operation enforces its own immediate constraints; the API does not coalesce a
group's net-zero totals or defer uniqueness across calls. Python patch_many remains
the grouped mutation interface.

Hierarchical whole insert/replace/delete labels return operation without writes.
Non-leaf child insertion/deletion, embedded or nullable containers, and custom Python
validators remain unsupported. Supplier still fails export because SupplierProduct
has a custom model validator. Up to 64 keyed declarations with 16 fields per owner
are supported. New statuses include operation, invalid_path, collection_limit, and
node_budget, alongside the ordinary statuses above and transaction for a participant
without a TP. Failed operations leave data, metadata, totals, counts, and indexes intact.

Regenerate source after schema changes; old bindings fail rather than bypass new
totals. No layout conversion or root metadata installation occurs through child
operations. M enforces declared model rules, not inventory service rules or ledger
workflows. The complete inventory ledger still needs unsupported M temporal/embedded
types and application business checks. Root creation/replacement remains a Python
operation.

## Compose writes in a caller transaction

Newly exported routines also provide inserttx(input), replacetx(input), and
deletetx(key). These participants require an existing transaction and return transaction if none is active. A successful
participant leaves the caller's transaction level unchanged and never commits it.

```m
 tstart ():serial
 set status=$$inserttx^PartAPI(.first)
 if status'="ok" quit
 set status=$$inserttx^PartAPI(.second)
 if status'="ok" quit
 tcommit
```

Supply complete field inputs as above. This code belongs in a callable M routine,
with its own input/error reporting. The two participants may come from different
exported models. Index and schema changes commit with both records.

Every participant failure status aborts the entire active transaction, including
all nesting levels and earlier successful operations. Unexpected errors also roll
back before propagating to the caller's handler. After failure, tlevel is zero.
The caller must stop the workflow instead of proceeding as though earlier writes
survived. An attempted commit of that failed transaction cannot commit partial data.
Existing insert/replace/delete still return nested without rolling back the caller.

The caller owns the outer restart point. Re-read every database-derived value after
tstart, including values used to build inputs, so a native restart recomputes the
whole business operation. Reset any paths or inputs modified during the attempt;
TSTART ():serial does not restore local variables. Data-dependent failure statuses
also request stable revalidation before aborting. Stable IDs and external inputs
can be prepared before the restart point.
Avoid external effects in retryable code. Participants do not make detached inputs
safe for dependent calculations. Regenerate existing routines to obtain these entry
points; schema metadata and record bytes do not change.

## Write references from M

```python
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Reference, Registry


class CatalogProduct(Model):
    __global__ = "^MReferenceProduct"
    id: str = Field(primary_key=True, min_length=1)
    name: str = Field(min_length=1)


class CatalogLot(Model):
    __global__ = "^MReferenceLot"
    id: str = Field(primary_key=True, min_length=1)
    product: Reference[CatalogProduct] = Field(index=True)


catalog = Database(MemoryStore(), Registry(CatalogProduct, CatalogLot))
product_source = catalog.m_routine(CatalogProduct, name="ProductAPI")
lot_source = catalog.m_routine(CatalogLot, name="LotAPI")
```

```m
 set product("id")="P1",product("name")="Widget"
 set status=$$insert^ProductAPI(.product)
 set lot("id")="L1",lot("product")="P1"
 set status=$$insert^LotAPI(.lot)
```

Supply the target's plain key, without the stored r: or s: prefixes. The declared
target key codec determines its interpretation. M reference keys use the current
ASCII string or canonical 18-digit integer subset. Target creation must precede a
new link. Participants can create the target and source in the same caller TP.

Export binds each registered target's exact schema snapshot and fingerprint,
including supported legacy metadata. Writes recheck those bindings and managed
record metadata inside the transaction. Targets may be Python-managed models
outside the M writer subset; linking does not rewrite or load their aggregates.
After a target schema changes, regenerate referring routines even when the source
schema's global/key reference identity remains unchanged.

Record fields, secondary/unique indexes, reverse entries, and inbound counts commit
or roll back together. Counts use exact bounded digit-string arithmetic. Missing or
untrusted targets and corrupt edge/count metadata leave no partial source records
or metadata installation. Self-reference insertion can see its own staged record;
M deletion still conservatively refuses every active incoming reference.

A target under maintenance blocks new links and full replacements that validate
that target. Deleting a source can release its existing links during target
maintenance, as Python permits. Concurrent target deletion and link creation cannot
commit a dangling managed reference. Raw writes remain outside these guarantees.
Customized Reference key validation, configuration, constraints, and aliases are
rejected at export rather than omitted.

## Decimal input contract

Decimal fields accept canonical Python Decimal text whose value is exactly
representable in integer units of 1E-18, with at most 40 unit digits. Comparisons,
bounds, and equality normalization use exact text operations rather than native M
numeric arithmetic. Record nodes retain the supplied canonical scale, including
signed zero, while equality indexes normalize equivalent values. A scale-only
replacement keeps its unique identity.

Numeric bounds must fit that unit domain. Precision and decimal-place limits must
be bounded integers at most 32,766, with a positive max_digits. M applies those
limits to the supplied encoding's digits and scale directly. It does not rely on
Pydantic's additional context-dependent normalize() relaxation. For example, a
field with decimal_places=6 accepts 0.100000 but rejects 0.1000000 through M even
when Python could normalize the latter to 0.1. Supply a shorter equivalent
encoding when appropriate. Scientific notation must match Python's canonical str
form, with an exponent of at most 18 digits. Nonfinite inputs are rejected.

This is an explicit M input subset. Python retains its full finite Decimal domain
and existing validation behavior. A value outside this subset remains readable
and writable through Python. Stored values use normalized precision checks within
the exact unit domain, so trailing zeros alone do not prevent replacement/deletion.
Values outside that domain return unsupported. No schema or record conversion
occurs. Unsupported Decimal constraints, including multiple_of and bounds finer
than the unit precision, fail export rather than being ignored.

Other scalar extensions remain separate: nullable values, date/time values,
Enum/Literal fields, lists, non-ASCII strings, and arbitrary patterns still fail
export. For owned trees, see [mutate a keyed hierarchy](#mutate-a-keyed-hierarchy).

## Raw writes from M

Wrap related changes in a YottaDB transaction (`tstart` and `tcommit`), so mapper clients
never see a partial change. The table shows what each kind of write does to later mapper
operations.

| M write | Mapper result |
| --- | --- |
| A correctly tagged value to an unindexed scalar without a stored dependency, within its constraints | Works. `get` returns the new value. |
| An untagged value, such as `set ^X(...)=5` | `get` raises `StorageError`. |
| A tagged value that breaks a declared constraint | `get` raises `ValidationError`. |
| A new value in an indexed field | A query on the old value raises `StorageError`. A query on the new value returns nothing. `check_indexes` reports `missing_or_corrupt_entry`. |
| A new child without updating the collection's `c:N` | `get` raises `StorageError`. |
| A new child with the complete subtree and the updated `c:N` | Works for an unindexed child with no references or stored dependencies. |

The mapper refuses to read a bad write and does not repair it. Until you restore the value,
that record is unreadable through the mapper.

M writes bypass the following:

- Pydantic validation, including custom validators.
- Equality indexes and unique claims. A [rebuild](index-rebuilds.md) restores these.
- Reference existence checks, reverse entries, and inbound counts. No tool repairs these.
- Stored sums and their declared dependencies. Incremental updates assume managed inputs.

Stop raw writers during [migrations](migrations.md#stop-other-clients-first) and
[index rebuilds](index-rebuilds.md).

## Resolve a managed raw address

The public `Store` and `Path` are the byte-level interface. Inside `atomic`, use
`tx.raw_path(Model, key, at=...)` to resolve a managed root or owned object without
loading its aggregate. Global names come from registered application declarations;
caller-supplied keys become encoded subscripts. The helper checks the live installed
schema, record metadata, and owned markers. It never installs metadata or adopts raw data.

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry
from mccann_hub.yhmf.codecs import decode_scalar, encode_scalar


class Counter(Model):
    __global__ = "^RawCounter"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal(0), ge=0)


db = Database(MemoryStore(), Registry(Counter))
db.insert(Counter(id="C", quantity=Decimal(4)))


def raw_increment(tx):
    path = tx.raw_path(Counter, "C").child(b"quantity")
    value = decode_scalar(tx.store.get(path))
    assert isinstance(value, Decimal)
    tx.store.set(path, encode_scalar(value + Decimal(1)))


db.atomic(raw_increment)
assert db.get(Counter, "C").quantity == Decimal(5)
```

This example changes a valid unindexed scalar. Raw Store operations bypass Pydantic
validation, indexes, unique claims, references, child counts, and application rules.
A valid type tag does not guarantee those invariants. In ordinary application code,
use `patch`, child operations, or `save` to maintain them.

`raw_path` requires an active callback. Use the returned address and Store operations
in that transaction; a Path retained afterward is only an address and carries no
snapshot or authorization. Uncaught exceptions roll back the callback, and native
restart signals retain their control semantics. Caught raw-operation errors do not
set the mapper's failed-write flag. Raw access does not supply record permissions
or make direct M writes maintain bookkeeping.
