# You have MUMPS, friend

YHMF maps explicit Python model operations to inspectable YottaDB globals.
The distribution is `mccann-hub-yhmf`, imported as `mccann_hub.yhmf`.
Python 3.12 or later is required. This is an early prototype under GPL-3.0-or-later.

The [architecture](docs/yottadb_hierarchical_mapper_architecture.md) describes
the intended milestones. The first implementation supplies a versioned storage
contract, scalar codecs, and native and in-memory storage adapters.

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
