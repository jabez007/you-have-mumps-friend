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

The generator supports flat qualified scalar and Reference models with ordinary equality
and unique indexes. It enforces string length and numeric gt/ge/lt/le bounds, with
valid UTF-8 string values and canonical signed integers of at most 18 digits. Strings
may contain controls; NUL is forbidden in keys. String length constraints count
Unicode codepoints, independently of the M process character set. Python retains its
full scalar integer domain. An existing record outside the M stored-value domain
returns unsupported without changes. Use Python for those records.

Export rejects custom Python validators, unsupported constraints or normalization,
nullable embedded objects, scalar lists, and more than 16 fields per owner. Keyed trees have the separate operations
below. The generator does not silently omit rules. Every inserted non-derived scalar field must be supplied even if Python declares a
default; defaults/factories are not run. Empty-root/branch creation derives zero
stored totals and creates empty declared collections.
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
Python would accept the record. For example, an existing integer over 18 digits, or a Decimal outside the fixed-unit domain is refused without writes.
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
The outermost TP must permit restarts, as described under
[caller transactions](#compose-writes-in-a-caller-transaction).
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
Populated non-leaf deletion, nullable containers, and custom Python
validators remain unsupported. Supplier still fails export because SupplierProduct
has a custom model validator. Up to 64 keyed declarations with 16 fields per owner
are supported. New statuses include operation, invalid_path, collection_limit, and
node_budget, alongside the ordinary statuses above and transaction for a participant
without a TP. Failed operations leave data, metadata, totals, counts, and indexes intact.

Regenerate source after schema changes; old bindings fail rather than bypass new
totals. No layout conversion or root metadata installation occurs through child
operations. M enforces declared model rules, not inventory service rules or ledger
workflows. The complete inventory ledger still needs unsupported M embedded
contracts and application business checks. Populated root creation/replacement remains a Python operation.

New exports memoize SHA-256 of exact serialized locations in the reserved process-local
%YHMFDigestCache variable. At most 128 entries of at most 4,096 input bytes are retained;
the entries are cleared at capacity. This caches a pure function, never schema,
record, index, reference, count, or transaction state. Normal M NEW/KILL and TP local
variable restoration determine cache lifetime. A reused entry remains valid across
restarts because its answer depends only on its exact input bytes. KILL of this
reserved variable clears only the cache. Regenerate older routines to obtain it.

## Compose writes in a caller transaction

Newly exported routines also provide inserttx(input), replacetx(input), and
deletetx(key). These participants require an existing transaction and return transaction if none is active. A successful
participant leaves the caller's transaction level unchanged and never commits it.

The outermost transaction must enable restarts. Use TSTART (), a saved-variable
list, or TSTART *. SERIAL is optional. A nested TSTART () does not enable restarts
for a bare outer TSTART. This requirement applies even to failures detected before
database writes, because participants revalidate failure statuses with TRESTART.
See the [YottaDB TSTART contract](https://docs.yottadb.com/ProgrammersGuide/commands.html#tstart).

```m
 tstart ()
 set status=$$inserttx^PartAPI(.first)
 if status'="ok" quit
 set status=$$inserttx^PartAPI(.second)
 if status'="ok" quit
 tcommit
```

Supply complete field inputs as above. This code belongs in a callable M routine,
with its own input/error reporting. The two participants may come from different
exported models. Index and schema changes commit with both records.

Within this restartable caller contract, every participant failure status aborts the
entire active transaction, including all nesting levels and earlier successful operations. Unexpected errors also roll
back before propagating to the caller's handler. After failure, tlevel is zero.
The caller must stop the workflow instead of proceeding as though earlier writes
survived. An attempted commit of that failed transaction cannot commit partial data.
Existing insert/replace/delete still return nested without rolling back the caller.

Bare TSTART is unsupported. A successful call under a bare TSTART does not qualify
that caller for failure handling or concurrency. A failure can raise TRESTNOT and
TRESTLOC instead of returning a status. Restart refusal can bypass the writer's local
error trap and reach the caller with a positive tlevel after staged data is restored.
The caller's error handler must TROLLBACK any active TP and end the failed workflow.
Do not rely on an unsupported caller automatically reaching tlevel zero.

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
UTF-8 string or canonical 18-digit integer subset. Target creation must precede a
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

Other scalar extensions remain separate: lists and arbitrary patterns still fail
export. For owned trees, see [mutate a keyed hierarchy](#mutate-a-keyed-hierarchy).

## Patch a root or a non-leaf owner

patch(key,input) changes explicitly supplied root scalars; patchtx(key,input) is
its caller-transaction participant. Both flat and hierarchical roots are supported:

```mumps
 set changes("name")="Receiving warehouse"
 set status=$$patch^WarehouseAPI("MAIN",.changes)
```

For a non-leaf owned record, patchchild/patchchildtx use its ordinary concrete path:

```mumps
 set path(1)="locations",path(2)="DOCK"
 set changes("label")="Receiving"
 set status=$$patchchild^SiteAPI("MAIN",.path,.changes)
```

The declared model must actually contain the scalar being patched. Every patch
requires at least one supplied field. Keys, derived sums, and whole child collections
cannot be patched. Qualified nullable scalars use the explicit null form below.
Validation reads this owner's scalars and immediate collection markers; it does not
load or validate unrelated descendants. Only affected indexes and references change.
Child nodes, collection counts, and descendant totals remain intact. This is a bounded
mutation, not a complete aggregate audit. Unsupported validators still fail export.

Normal transaction, maintenance, reference, and uniqueness checks apply on every
attempt. Replace remains a complete-record operation. Populated tree construction/deletion remains unqualified until its separate
issue-53 slice. Existing storage bytes and schema identities are unchanged;
regenerate source for these patch interfaces.

## Empty owned branches

insertchild/insertchildtx can create an empty non-leaf child, such as a Location
or Bin. Supply all non-derived scalars, omit its child collections, and omit stored
totals. The writer creates c:0 markers for the collections and derives zero totals,
validating them normally. Application defaults/factories are not run. A collection
whose minimum length is positive returns collection_limit; invalid zero totals fail
validation. Supplied collection markers or derived values return invalid.

```mumps
 set path(1)="locations",path(2)="DOCK",path(3)="bins"
 set input("id")="B2"
 set status=$$insertchild^WarehouseAPI("MAIN",.path,.input)
```

Delete the empty branch with deletechild/deletechildtx and its concrete path. The
same transaction maintains parent counts, indexes, references, and any ancestor sums.
A branch containing children returns operation without writes. This operation does
not recursively delete populated data. Root construction/deletion and complete tree replacement retain their separate
contracts below.

## Empty hierarchical roots

insert/inserttx can create an empty hierarchical root with its required non-derived
scalars. Child collections are implicit empty collections; stored totals are derived
as zero. For example:

```mumps
 set input("id")="WEST",input("name")="West warehouse"
 set status=$$insert^WarehouseAPI(.input)
```

The first insert installs the exact schema metadata only if the application global
and its framework index/reference namespaces are empty, in the same transaction.
Raw records at any key return unmanaged. Concurrent first writers revalidate after
native restarts. Caller participants roll back initial metadata along with records
and indexes if a later participant fails. This never implicitly adopts existing data.

Delete/deletetx can remove an empty hierarchical root, enforcing incoming reference
restrictions. A populated root returns operation without writes. Root replacement
and populated tree construction/deletion remain unqualified. Use explicit child
inserts to build the hierarchy incrementally. Application defaults/factories are not
run; supplied collection markers/derived totals are invalid. Existing schemas/data
need no conversion. Regenerate source for these capabilities.

## Finite string and integer choices

UTF-8 string and integer Enum/Literal fields accept their exact primitive values, using
ordinary scalar record tags and equality/unique indexes. Enum member names and
aliases are not wire values. For example, Literal["receive", "move", "adjust"]
accepts input("kind")="receive" and rejects "unknown" with invalid. Existing
out-of-domain stored members return corrupt; failed calls leave all state intact.
The same rules apply to owned-child insert/patch/delete.

Each exported domain is limited to 256 distinct members and 32,764 bytes of escaped
scalar JSON. All members must fit the UTF-8 string or canonical signed 18-digit
integer domain; unsupported members reject the entire export. Controls and long
options are escaped and chunked in generated source. Boolean choices remain unqualified. Custom validators, Enum core-schema validation
hooks, and unsupported constraints still fail export. Python Enum fallback hooks are not run.

Regenerate source to use this capability. Existing choice schemas and record/index
bytes are unchanged. Changing an application's plain string field to a finite choice
remains a schema change requiring its normal reviewed migration.

## Calendar and UTC input contract

Date fields accept YYYY-MM-DD in the proleptic Gregorian calendar, years 0001–9999.
Datetime fields accept YYYY-MM-DDTHH:MM:SS.ffffffZ with exactly six fractional digits
and UTC Z. Hours are 00–23; minutes and seconds are 00–59. Leap seconds, naive text,
offset spellings, shortened dates, and variable fractional precision return invalid.
Python still accepts representable aware offsets and stores their canonical UTC
instant; M requires that canonical text as input. For example, use
input("occurred_at")="2026-11-07T12:30:00.000000Z".

Calendar/UTC bounds use exact lexical comparisons of canonical text. Equality and
unique indexes use the existing temporal-v1 bytes, D: for dates and T: for instants.
Existing Python temporal values can be replaced or deleted without conversion;
invalid calendar/UTC stored text returns corrupt. Custom validators remain separately unqualified. Regenerate source for the new writer
capability; schema snapshots and record/index layouts do not change.

## Explicit nullable scalar inputs

For a qualified nullable scalar or choice, pass input("field","%null")=1 to write
None. The field must have no scalar value and no other children; the flag must be
exactly "1". Non-null input stays input("field")=value. Empty strings, "n:", "v:",
and "%null" are ordinary string values, never null sentinels. Omitted fields remain
invalid for insert/replace, even when the Python declaration defaults to None.
A patch can omit unrelated fields.

Nullable inputs retain the existing n: null node. Null equality entries remain
queryable, and null unique fields have no claim, permitting multiple nulls. Changes
between null and a value maintain the same indexes and claims as Python within the
transaction, including rollback and restart. Invalid null shapes return invalid;
malformed stored shapes remain corrupt. This applies to flat and owned scalar fields.
Nullable references, containers, lists, and unqualified scalar families still fail
export. No stored metadata or data conversion is needed; regenerate M source.

## Unicode strings and native identities

String fields, string choices, and string reference/ownership keys preserve valid
UTF-8 bytes. Length constraints count codepoints, including combining marks as
separate codepoints. No normalization or grapheme clustering is applied: "é" and
"é" remain distinct values and unique claims. JSON index values, claim identities,
and ownership locations use the same lowercase ASCII escapes and surrogate pairs
as Python's canonical JSON. Existing ASCII identities remain unchanged.

The decoder and quoting helpers operate on bytes in both M and UTF-8 character modes.
Malformed UTF-8 input returns invalid, and malformed stored strings return corrupt,
including truncated sequences, overlong encodings, surrogate encodings, and values
above U+10FFFF. The existing 32,764-byte scalar input ceiling, native key budgets,
and 4,096-byte encoded-location hashing limit still apply; Unicode escaping can use
more bytes than the input text. Exceeding a key/location budget returns key_size.
Regenerate source for Unicode support; schemas and record/index bytes need no change.

General pattern constraints remain rejected. Python regular expressions include
Unicode, flags, anchoring, and matching rules that have no general exact translation
to M pattern syntax. Use a finite Literal domain when it describes the actual rule,
with a reviewed schema migration if replacing an existing pattern declaration.

## Ordered index writes

Generated writers support ordered-v1 on qualified string, integer, Decimal, date,
UTC datetime, and finite-choice fields, including their qualified nullable forms.
They use the existing native numeric/lexical index keys and ordinary transactional
claim maintenance. Python ordered queries can read those writes immediately; no
new index namespace or metadata migration is introduced.

String keys escape NUL and SOH exactly as ordered-v1 specifies. Temporal keys keep
D:/T: bytes. Null uses z:null and has no unique claim. Nonzero ordered numerics require
at most 18 significant digits and magnitude from 1E-18 to below 1E18. M builds their
canonical native numeric text from exact coefficient/power values without native
rounding. Records retain their original scalar text, including Decimal scale.
Unsupported values fail validation before persistence; native key budgets still apply.

Root/owned patches touch only the affected ordered indexes. Whole replacements,
unique failures, rollback, and restarts use the same index transition code as equality
indexes. Regenerate source to enable this capability on existing ordered schemas.

## Complete non-null embedded objects

Complete insert/replace operations accept nested M arrays for non-null embedded
models. Their ordinary o:1 markers, scalar tags, dotted indexes, concrete location
digests, and reverse references match Python. For example, a declared Position with
warehouse/location/bin fields takes input("destination","warehouse")="MAIN",
input("destination","location")="DOCK", and input("destination","bin")="B1".
Supply all nested non-derived fields; storage markers are not input values.

The same owner validation/index/reference code runs recursively inside the outer
transaction. A nested failure restores root fields, claims, edges, and initial schema
metadata together. Native restarts repeat the whole write. Scalar patches preserve
untouched embedded subtrees; they reject embedded-object replacement through patch.
A model containing Children still cannot use complete tree replace.

This qualification covers nonempty embedded models without child collections inside
them, to 14 declared ownership segments, 64 declarations, and 16 fields per owner.
Custom validators and unsupported field/container constraints remain rejected.
Embedded rollup sources and nullable embedded containers remain separate work.
Fixed embedded node widths participate in the ordinary 10,000-node record budget,
including keyed-child insertion. No schema/data conversion is required; regenerate
source for these capabilities.

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
