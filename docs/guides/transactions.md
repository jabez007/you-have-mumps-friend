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

save compares the complete desired aggregate with storage and writes only physical
node differences. It still reads and validates the aggregate, and its physical audit
traverses existing nodes to remove raw extras. A stale field value still replaces a
newer stored value: a difference cannot infer your intended edits. Use patch, child
operations, or atomic read-modify-write for targeted updates. save of an unchanged
managed value writes nothing, while still checking schema, validation, and indexes.

Replacement deletes minimal removed subtrees and preserves desired descendants if
an unexpected raw value occupies their parent. A raw subtree beyond the bounded
physical traversal budget raises StorageError without writes; repair it explicitly.
No storage layout, installed schema identity, or detached concurrency rule changes.

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

Test known external boundaries by replacing them with a mock that fails if called
inside the callback. For example, continuing with the Product above:

```python
from unittest.mock import Mock

publish = Mock(side_effect=AssertionError("External side effect inside transaction"))


def unsafe_notification(tx):
    tx.patch(Product, "P42", changes={"quantity": Decimal("7")})
    publish("stock changed")


try:
    db.atomic(unsafe_notification)
except AssertionError:
    pass
else:
    raise AssertionError("Expected the instrumented boundary to reject the callback")

publish.assert_called_once_with("stock changed")
assert db.get(Product, "P42").quantity == Decimal("8")
```

This checks only the boundary you replaced. Python cannot reliably detect all side
effects in arbitrary callbacks. Keep real publication after successful commit and
decide separately how the application recovers if it crashes before publication.

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

Calling `atomic` through the transaction handle, the original database handle, or
another handle sharing its backend raises `TransactionError`. The inner callback
does not execute. An uncaught nesting error rolls back the outer transaction.

Memory stores are independent databases, so separate memory-store instances can
run independent transactions. Native-store instances share YottaDB's nonthreaded
engine and cannot open nested transactions through another instance.

## Do not swallow restarts

The native adapter remembers restart and rollback signals received by its storage
calls. Catching one inside a callback cannot turn that attempt into a successful
commit. Subsequent native calls reject the invalid attempt, and the original signal
reaches YottaDB when the callback ends. A restart creates fresh attempt state.

Catch only the specific business errors you expect, such as `NotFound`. Broad
handlers can still hide application mistakes. Direct calls to the `yottadb` module
bypass the adapter's signal tracking and must obey its native exception contract.
Callbacks and validators must remain free of external side effects.
