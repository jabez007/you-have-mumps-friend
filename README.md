# You have MUMPS, friend

YHMF maps explicit Python model operations to inspectable YottaDB globals.
The distribution is `mccann-hub-yhmf`, imported as `mccann_hub.yhmf`.
Python 3.12 or later is required. This is an early prototype under GPL-3.0-or-later.

The [architecture](docs/yottadb_hierarchical_mapper_architecture.md) describes
the intended milestones. The prototype supplies a versioned storage contract,
scalar models, explicit CRUD, and native and in-memory storage adapters.

## Persist a model

```python
from decimal import Decimal
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry

class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)

db = Database(MemoryStore(), Registry(Product))
db.insert(Product(id="P42", quantity=Decimal("12.50")))
product = db.get(Product, "P42")
product.quantity -= Decimal("2")
db.save(product)
assert db.get(Product, "P42").quantity == Decimal("10.50")
```

Use `NativeStore()` with a configured YottaDB environment for durable storage.
`MemoryStore()` lasts only as long as the Python process. `insert` rejects existing
keys; `save` replaces a record or inserts it. `get` raises `NotFound` when absent,
and `delete` returns whether a record existed. Keys must match their declared type.

`db.atomic(callback)` passes a transaction handle to the callback. The handle
expires when the callback returns. Each restart creates a fresh handle and
reconstructs records from storage. All standalone operations also use transactions.
Nested `atomic` callbacks are rejected.

The registry rejects overlapping globals, aliases, unsupported field types,
and missing or ambiguous primary keys. Access checks an installed schema
fingerprint, and incompatible declarations raise `SchemaMismatch` before writes.
Changing a model name, field declaration, default, or constraint can change the
fingerprint. There is no migration engine yet. `index` and `unique` flags fail
explicitly until secondary indexes are implemented.

## Run development checks

Install [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
```

Ruff checks cyclomatic complexity with a maximum of 10. Native tests skip
unless you explicitly select a disposable database. The in-memory adapter does
not prove native conflict handling or process concurrency.

## Run native storage tests

Install [YottaDB](https://docs.yottadb.com/AdminOpsGuide/installydb.html) and
set `ydb_dist` to its installation directory. Then run:

```sh
uv sync --extra native
bash scripts/test-native.sh -q
```

The script creates and removes a dedicated temporary database. Native CI runs
this suite with YottaDB r2.02 and Python 3.12 on Ubuntu 22.04. An unpacked binary
archive alone is insufficient because YottaDB requires a correctly installed
`gtmsecshr` helper. Setting `YHMF_TEST_NATIVE=1` manually opts into tests against
your current database; use only disposable data.

YDBPython uses process-based concurrency. Callbacks passed to `atomic()` can
execute repeatedly after transaction conflicts, so keep external side effects
outside the callback.

YottaDB and YDBPython have separate upstream licenses. This repository does
not provide legal advice about applications that combine them with this code.
