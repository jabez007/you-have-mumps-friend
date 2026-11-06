# Change part of an aggregate

Use these operations to change one owned value or child without rewriting the whole
aggregate. The examples share this setup:

```python
from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry


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
```

## Patch scalar fields

```python
db.patch(Warehouse, 7, changes={"name": "Renamed"})
db.patch(
    Warehouse,
    7,
    at=("bins", "A01", "lots", "L-001"),
    changes={"quantity": Decimal("25.5")},
)
lot = db.get(Warehouse, 7, at=("bins", "A01", "lots", "L-001"))
assert lot.quantity == Decimal("25.5")
```

Each path starts at the registered root key. An embedded object takes a field name.
Keyed children take a collection field, then a key of the child's declared type.

A patch changes scalar or `Reference` fields only. Changing keys or replacing owned
subtrees through `changes` raises `MutationError`. Mutations never create missing
objects or collections. A missing owner raises `NotFound`.

A null collection raises `NotFound`; malformed markers and unexpected descendants
under a null marker still raise `StorageError`.

## Do arithmetic in a transaction

Read the owned value inside the callback:

```python
def receive(tx):
    at = ("bins", "A01", "lots", "L-001")
    current = tx.get(Warehouse, 7, at=at)
    tx.patch(Warehouse, 7, at=at, changes={"quantity": current.quantity + Decimal("1")})


db.atomic(receive)
assert db.get(Warehouse, 7, at=("bins", "A01", "lots", "L-001")).quantity == Decimal("26.5")
```

The callback may restart, so follow the [callback rules](transactions.md#write-callbacks-that-can-run-again).
Failed partial mutations make the transaction roll back, even if the callback catches
the exception.

## Insert and delete keyed children

```python
db.insert_child(
    Warehouse,
    7,
    collection=("bins", "A01", "lots"),
    record=Lot(number="L-002", quantity=Decimal("3")),
)
db.delete_child(Warehouse, 7, at=("bins", "A01", "lots", "L-002"))
```

Insertion requires an existing collection and takes the child key from the validated
record. It rejects existing child data and validates the complete new subtree.

Deletion requires an existing keyed child. It removes the subtree, releases the
subtree's outgoing references and unique claims, and keeps the siblings.

Both operations update the collection count and enforce collection length constraints.
Persistence revalidates plain declared owned data, so unchecked `model_copy` values
cannot bypass constraints through `revalidate_instances="never"`.

Insertion checks the aggregate node budget from transactional ownership markers. Flat
child collections use their count and declared width; variable-width owned containers
require bounded traversal. It does not load the whole root model. See the
[native insertion measurements](../performance.md#child-insertion-results).

## Which validators run

Partial changes never skip a validator to run faster. They refuse the change instead.

| Target | Validation |
| --- | --- |
| A leaf model | The complete leaf model, including cross-field validators. |
| An owner with descendants | Selected fields, with their constraints and model configuration. |
| Custom validation on such an owner or any ancestor | Raises `MutationError`. |

Custom validation here includes `Annotated` function validators, custom initialization,
and post-init hooks. For those models, use a complete `get` and `save` inside `atomic`.
Child insertion and deletion share these ancestor restrictions.

## What a concurrent change does

- A patch reads its affected state inside the transaction and writes only changed scalar
  nodes. It maintains only the affected indexes and reference entries, and does not
  rewrite sibling collections.
- Patches to different fields keep both assignments. Patches to the same field serialize,
  and the later commit wins.
- A parent or child deletion that races a nested update causes a restart. If the delete
  wins, the update raises a missing-owner error on retry.
- Unique-index conflicts allow one claimant.
- Failure and restart keep nodes, collection counts, indexes, and reference entries
  together.

[ADR 0004](../adr/0004-hierarchical-mutations.md) covers the validation and storage
tradeoffs.
