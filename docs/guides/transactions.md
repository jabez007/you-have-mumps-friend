# Use transactions

Standalone operations such as `insert` and `save` each run in their own transaction.
Use `db.atomic` when several operations must commit together, or when a write depends
on a value you read.

## Read and change a value safely

Read inside the callback and write through the handle that the callback receives:

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)


db = Database(MemoryStore(), Registry(Product))
db.insert(Product(id="P42", quantity=Decimal("10")))


def take_two(tx):
    product = tx.get(Product, "P42")
    product.quantity -= Decimal("2")
    tx.save(product)


db.atomic(take_two)
assert db.get(Product, "P42").quantity == Decimal("8")
```

A read outside the callback gets no protection. Saves outside `atomic` use
last-writer-wins: two readers can each change a copy, and the later save replaces all
of the earlier result. [ADR 0003](../adr/0003-detached-write-concurrency.md) explains
this choice.

## Write callbacks that can run again

YottaDB uses optimistic transactions. When transactions conflict, it restarts the
callback. Follow these rules in every callback:

- Keep external side effects, such as sending email, outside the callback.
- Build all state from reads through the handle. Each attempt gets a fresh handle
  and reconstructs records from storage.
- Do not catch broad exceptions such as `Exception` around operations on the handle.
  See the [warning on swallowed restarts](#do-not-swallow-restarts).
- Keep validators deterministic and free of side effects, because they run again too.

YDBPython uses process-based concurrency, so use processes for parallel database work.

## How callbacks end

The handle expires when the callback returns. Using it later raises `TransactionError`.

The outcome depends on how the callback ends:

| Callback result | Effect |
| --- | --- |
| Returns normally | Commits and returns the callback's value. |
| Raises a mapper or value-validation error | May rerun first, so the adapter can revalidate through YottaDB's final stable attempt. A persistent error then rolls back and raises its original type. See [ADR 0005](../adr/0005-native-validation-restarts.md). |
| Raises `RuntimeError` | Aborts and rolls back. |
| Raises a native control or error signal | Passes through. |

If a backend write fails, the transaction must roll back, even when the callback catches
the exception. Later operations on that handle raise `TransactionError`.

## Do not nest transactions

Calling `tx.atomic` inside a callback raises `TransactionError`.

> **Warning:** The guard does not cover the outer handle yet. Calling `db.atomic` inside
> a `db.atomic` callback runs like a savepoint instead of raising. Do not rely on this
> behavior. See [known limitations](../known-limitations.md).

## Do not swallow restarts

> **Warning:** A restart that reaches a broad `except` inside a callback can commit that
> attempt with the wrong data. In a live run with four writers, a callback that caught
> `Exception` around a read and carried on with a fallback value lost most of its
> increments. The same workload lost none with the read outside any `try`. See
> [known limitations](../known-limitations.md).

Catch only the specific errors you expect, such as `NotFound`.
