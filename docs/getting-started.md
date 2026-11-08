# Get started

In this tutorial, you store a warehouse with bins and lots, change it safely, and query
it. You need Python 3.12 or later. It uses the in-memory store, so you do not need
YottaDB.

## Install YHMF

```sh
python -m venv .venv
. .venv/bin/activate
pip install "git+https://github.com/jabez007/you-have-mumps-friend"
```

Create a file called `demo.py`. Add each block below to the end of the file.

## Declare the models

A warehouse owns bins, and a bin owns lots. Only the top-level model has a global:

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
    name: str = Field(default="Main", index=True)
    bins: Children[Bin] = ChildField(key="code")


db = Database(MemoryStore(), Registry(Warehouse))
```

## Store a record

```python
db.insert(
    Warehouse(
        id=7,
        bins={"A01": Bin(code="A01", lots={"L-001": Lot(number="L-001", quantity=Decimal("12.50"))})},
    )
)
print(db.get(Warehouse, 7).bins["A01"].lots["L-001"].quantity)
```

Run `python demo.py`. It prints `12.50`.

## Let validation reject bad data

The `ge=0` on `quantity` rejects a negative value before anything reaches storage:

```python
from pydantic import ValidationError

at = ("bins", "A01", "lots", "L-001")
try:
    db.patch(Warehouse, 7, at=at, changes={"quantity": Decimal("-1")})
except ValidationError as error:
    print("rejected:", error.errors()[0]["type"])
print(db.get(Warehouse, 7, at=at).quantity)
```

It now also prints `rejected: greater_than_equal` and `12.50`.

## Change a value in a transaction

Read inside the callback, so a conflicting writer makes the callback run again with
fresh data:

```python
def receive(tx):
    lot = tx.get(Warehouse, 7, at=at)
    tx.patch(Warehouse, 7, at=at, changes={"quantity": lot.quantity + Decimal("1")})


db.atomic(receive)
print(db.get(Warehouse, 7, at=at).quantity)
```

It now also prints `13.50`.

## Query an index

`name` has `index=True`, so you can filter on it. Filtering on a field with no index
raises an error and never scans:

```python
print([warehouse.id for warehouse in db.query(Warehouse).where(name="Main").all()])
print(db.query(Warehouse).where(name="Main").explain().strategy)
```

It now also prints `[7]` and `equality_index`.

## Next steps

- [Set up a YottaDB database](guides/native-setup.md) to store data durably.
- [Read the data from M](guides/m-access.md) to see how this record looks in globals.
- Run the [warehouse inventory example](../examples/inventory/README.md) for a larger
  application.
- See the [documentation map](README.md) for every guide.
