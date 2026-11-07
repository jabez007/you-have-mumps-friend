# Adopt an existing global

YHMF installs a schema automatically only when the application global is empty,
including its root value and all descendants. Raw data at any key prevents installation.
A failed installation rolls back together with the record write.

If a small global is already encoded exactly in [layout one](../storage-format.md), adopt
it explicitly from outside `atomic`:

```python
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Registry


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)


store = MemoryStore()
store.set(Path("^Product", (b"s:P42", b"id")), b"s:P42")  # Data that is already there.

db = Database(store, Registry(Product))
assert db.adopt(Product) == 1
assert db.get(Product, "P42").id == "P42"
```

Adoption has these requirements:

- No installed schema metadata, equality or unique namespaces, or inbound-reference
  metadata exists yet.
- The global holds at most 100 records and 10,000 encoded record nodes.
- Each record has either no `%m` metadata or complete matching metadata.

Adoption validates fields, child counts, keys, constraints, unique values, and reference
targets. It rejects unknown nodes, partial metadata, and arbitrary legacy encodings.

Stored field bytes stay as they are. Metadata and secondary structures install atomically,
so a failed adoption leaves all data and metadata unchanged.

Larger or differently encoded datasets need an external, explicitly reviewed migration.

## Read a foreign layout without adopting it

Use an explicit GlobalView for native globals with another application's format.
The model declares the returned values; MappedField declares relative native paths
and pure byte decoders. For example, plain M can write:

```m
 set ^LegacyStock("items",2,"N")="Widget"
 set ^LegacyStock("items",2,"Q")=25
```

The equivalent runnable projection is:

```python
from mccann_hub.yhmf import GlobalView, MappedField


class LegacyPart(Model):
    __global__ = "^LegacyStock"
    id: int = Field(primary_key=True)
    name: str
    quantity: int = Field(ge=0)


def utf8(raw: bytes) -> str:
    return raw.decode("utf-8")


def integer(raw: bytes) -> int:
    return int(raw.decode("ascii"))


foreign = MemoryStore()
foreign.set(Path("^LegacyStock", (b"items", b"2", b"N")), b"Widget")
foreign.set(Path("^LegacyStock", (b"items", b"2", b"Q")), b"25")
view = GlobalView(foreign, LegacyPart, root=Path("^LegacyStock", (b"items",)), fields={
    "name": MappedField((b"N",), utf8),
    "quantity": MappedField((b"Q",), integer),
})
assert view.get(2).quantity == 25
assert [part.id for part in view.scan(limit=10)] == [2]
assert view.scan(after=2) == []
assert foreign.get(Path("^LegacyStock", (b"items", b"2", b"Q"))) == b"25"
```

A view never installs schema metadata or indexes, adopts a record, or rewrites source
bytes. Its declarations must be flat scalar models without declared indexes. Map
each non-key field exactly once. Relative paths can be empty to decode a packed
value at the record node; several fields may decode different pieces of that one
node, fetched once per read attempt. Extra source nodes are outside the projection.

Keys are ordinary untagged native subscripts. The model's str/int primary-key type
determines decoding. Integer keys retain canonical 18-digit limits; string keys
follow native M collation and numeric-string aliases. An integer 2 and string "2"
address the same native subscript. Empty/NUL native keys remain unsupported by Path.
scan returns 1–1000 records, default 100, in native key order; use the last key as
after. Noncanonical keys that cannot match the declared key type fail explicitly.

Each get/scan uses one Store transaction. Pure decoders receive bytes, and the
complete model is validated, including model validators. A missing mapped node is
omitted from input, allowing declared defaults; missing required fields and invalid
values raise normal Pydantic validation errors. Decoder ValueError/ArithmeticError
becomes StorageError naming the field. A validator that changes the primary key is
rejected. Record/field and node-byte budgets remain bounded by the existing limits.

The view does not check installed ORM schema or promise foreign reference, ownership,
or index integrity. Foreign writers own their format and must use transactions to
avoid exposing partial business updates. Use normal Database CRUD for managed
records. A view is a read-only interpretation, not a second writable storage layout.
