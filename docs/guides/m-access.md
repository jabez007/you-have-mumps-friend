# Read and write the data from M

YHMF stores data as ordinary globals, so M routines can reach it without Python. Reads
work today. Writes work only within the limits below, because M code does not run Pydantic
validation or maintain indexes.

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

## Write from M

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
