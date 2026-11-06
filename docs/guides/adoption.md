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
