# Define models and owned data

YHMF has three ways to relate data, and each one means something different in storage.

| Relationship | Declare with | Stored | Identity |
| --- | --- | --- | --- |
| Embedded object | A field whose type is another `Model` | Under the parent | None of its own |
| Owned children | `Children[Child]` and `ChildField(key=...)` | Under the parent, by key | Parent path plus child key |
| Reference | `Reference[Target]` | As a key, with the target elsewhere | The target's own key |

Choose by ownership and lifecycle, not by reflex. Moving an owned child to another
parent changes its identity. If a record keeps its identity across moves, make it a
top-level model and use a [reference](references.md).

## Declare a top-level model

A top-level model has a `__global__` and exactly one primary key:

```python
from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
```

Field types are `str`, `int`, `bool`, `float`, `Decimal`, `bytes`, those types with
`| None`, embedded models, `Children`, and `Reference`. Keys must match their declared
type.

The registry rejects overlapping globals, aliases, unsupported field types, and missing
or ambiguous primary keys.

Models default to `extra="forbid"`.

> **Warning:** Do not set `extra="allow"`. Extra values are not stored and read back
> as missing. See [known limitations](../known-limitations.md).

## Choose the right write

| Operation | Use it to |
| --- | --- |
| `db.insert(record)` | Create a root and reject an existing key. |
| `db.save(record)` | Replace a whole aggregate, or insert it if absent. |
| `db.patch(...)` | [Change scalar or reference fields](partial-updates.md). |
| `db.insert_child(...)`, `db.delete_child(...)` | [Change keyed ownership](partial-updates.md#insert-and-delete-keyed-children). |
| `db.delete(model, key)` | Delete a root. Returns whether a record existed. |

`db.get` raises `NotFound` when the record is absent.

## Declare owned children

Owned models have no `__global__`. The parent controls their persistence and deletion.
`Children` is a dictionary, and each key must match the child's primary key:

```python
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
db.insert(
    Warehouse(
        id=7,
        bins={"A01": Bin(code="A01", lots={"L-001": Lot(number="L-001")})},
    )
)
assert db.get(Warehouse, 7).bins["A01"].lots["L-001"].quantity == Decimal("0")
```

To coordinate updates across an aggregate, edit the loaded aggregate and save its root
inside [`atomic`](transactions.md). For direct changes to existing owned paths, use the
[partial updates](partial-updates.md) instead.

Ownership declarations cannot be recursive. Reads and writes also enforce
[size limits](../reference/limits.md) on collections and records.

## Keep persistent fields explicit

The registry rejects `extra="allow"` on a root or any owned model. Unknown values
accepted by that policy have no declared storage field. Declare them as model
fields instead. `extra="ignore"` remains available when input data intentionally
contains values that the application discards.

Disabling instance, default, or assignment validation does not bypass write-time
validation. The mapper rebuilds plain owned input and validates it before writing.
Setting `allow_inf_nan=True` does not permit nonfinite stored scalars. See the
[configuration compatibility policy](../schema-compatibility.md#model-configuration-and-declared-data).
