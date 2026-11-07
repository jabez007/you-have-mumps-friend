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
A routine name cannot collide with one of its generated entry labels; export rejects
such names before producing an uncompilable routine.
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

The generator supports flat non-null str/int/Decimal models with ordinary equality
and unique indexes. It enforces string length and numeric gt/ge/lt/le bounds, with
ASCII string values and canonical signed integers of at most 18 digits. Strings may
contain ASCII controls; NUL is forbidden in keys. Python retains its full Unicode
and scalar integer domains. An existing record outside the M value subset cannot
be changed/deleted with this routine. Use Python for those records.

Export rejects custom Python validators, unsupported constraints or normalization,
nullable values, owned trees, references as fields, scalar lists, choices, ordered
indexes, and more than 16 fields. It does not silently omit these rules. Every field
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
refuses any incoming reference namespace, including sources with cascade/set_null
policies; Python handles those policies. Newly exported routines validate the live
maintenance checkpoint and its schema barrier in the write transaction. They block
the affected model while unrelated writes can proceed. Corrupt or unknown checkpoint
formats fail closed. Existing exported routines retain their conservative global
gate check until regenerated; their installed schema bindings do not change.

The transaction-owning entry points return ok, exists, missing, invalid, invalid_key,
key_size, schema,
unmanaged, maintenance, corrupt, unique, references, or nested. Their failure
statuses leave the call's database state unchanged. Unexpected engine errors roll back the
owned transaction and propagate to the caller's error handler. They are not
reported as success or silently converted into a validation failure.

The [ADR](../adr/0013-explicit-m-writer-contract.md) records the trigger comparison
and initial contract. No trigger is installed by export or generated CRUD. Do not
combine these writers with external triggers that mutate managed data/indexes.
Hierarchical writers require further design. Since this generator rejects owned
trees, it cannot update stored-rollup inputs.
Raw writes remain outside the managed guarantees and are not an authorization path.

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
whole business operation. Stable IDs and external inputs can be prepared before it.
Avoid external effects in retryable code. Participants do not make detached inputs
safe for dependent calculations. Regenerate existing routines to obtain these entry
points; schema metadata and record bytes do not change.

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
and writable through Python. The generated M routine refuses to replace/delete
an existing record outside its supported subset. No schema or record conversion
occurs. Unsupported Decimal constraints, including multiple_of and bounds finer
than the unit precision, fail export rather than being ignored.

Other scalar extensions remain separate: nullable values, date/time values,
Enum/Literal fields, lists, non-ASCII strings, and arbitrary patterns still fail
export. Decimal support alone does not enable owned trees or references.

## Raw writes from M

Wrap related changes in a YottaDB transaction (`tstart` and `tcommit`), so mapper clients
never see a partial change. The table shows what each kind of write does to later mapper
operations.

| M write | Mapper result |
| --- | --- |
| A correctly tagged value to an unindexed scalar, within its constraints | Works. `get` returns the new value. |
| An untagged value, such as `set ^X(...)=5` | `get` raises `StorageError`. |
| A tagged value that breaks a declared constraint | `get` raises `ValidationError`. |
| A new value in an indexed field | A query on the old value raises `StorageError`. A query on the new value returns nothing. `check_indexes` reports `missing_or_corrupt_entry`. |
| A new child without updating the collection's `c:N` | `get` raises `StorageError`. |
| A new child with the complete subtree and the updated `c:N` | Works for an unindexed child with no references. |

The mapper refuses to read a bad write and does not repair it. Until you restore the value,
that record is unreadable through the mapper.

M writes bypass the following:

- Pydantic validation, including custom validators.
- Equality indexes and unique claims. A [rebuild](index-rebuilds.md) restores these.
- Reference existence checks, reverse entries, and inbound counts. No tool repairs these.

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
