# You have MUMPS, friend

Typed Python models for [YottaDB](https://yottadb.com/), stored as ordinary globals that
M code can read.

YHMF aims to give YottaDB what Odoo's ORM gives Postgres: declared models, validation,
relationships, indexes, and migrations. It does not treat the database as relational. You
declare models with [Pydantic](https://docs.pydantic.dev/), and a warehouse's bins and lots
live under the warehouse's own subscripts.

**This is an early prototype.** These docs describe the
development branch. The API and storage layout can change. It is not on PyPI yet. See
[what works today](#what-works-today).

## Example

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


db = Database(MemoryStore(), Registry(Warehouse))  # Use NativeStore() for YottaDB.
db.insert(
    Warehouse(
        id=7,
        bins={"A01": Bin(code="A01", lots={"L-001": Lot(number="L-001", quantity=Decimal("12.50"))})},
    )
)


def receive_one(tx):  # YottaDB runs this again if another transaction conflicts.
    at = ("bins", "A01", "lots", "L-001")
    lot = tx.get(Warehouse, 7, at=at)
    tx.patch(Warehouse, 7, at=at, changes={"quantity": lot.quantity + 1})


db.atomic(receive_one)
```

Before that transaction runs, `NativeStore` holds these nodes in YottaDB, plus four `%m`
metadata nodes:

```text
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

## How it differs from an ORM on SQL

- **Ownership is part of the data model.** Owned records are stored under their parent. A
  patch to one lot writes that lot's changed nodes, however many other lots exist.
- **M code can reach the data.** A plain `zwrite` or `$order` loop sees every record. M can
  also [write under limits](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/guides/m-access.md#write-from-m), and safer M writes are
  a project goal.
- **Queries have bounded plans.** A filter on an unindexed field raises `QueryError`, and
  `explain()` shows the plan. There is no fallback scan.
- **Schema changes are explicit.** A declaration that differs from the installed schema
  raises `SchemaMismatch` until you review and run a migration.

## What works today

This table includes unreleased changes on main. See
[the changelog](https://github.com/jabez007/you-have-mumps-friend/blob/main/CHANGELOG.md)
for release boundaries.

| Area | Works today | Not yet |
| --- | --- | --- |
| Models | Primitive scalars, dates, UTC datetimes, UUID values, Enum/Literal choices, scalar lists, optional fields, embedded objects, keyed children, Python field inheritance, virtual computed values | Stored rollups, shared-global variants |
| Relationships | Enforced references, explicit resolution, bounded inverse reads, owned many-to-many links, bounded `restrict`/`set_null`/`cascade` deletion | Unbounded or background cascades |
| Queries | Indexed equality, ordered range/prefix/ascending reads, unique indexes, root and child paging, occurrence projections, collection counts | Several predicates, descending order, ordered root cursors |
| Writes | Whole-record `save`, scalar `patch`, explicit `patch_many` groups, child insert and delete, restart-safe transactions | Change tracking, callback-wide deferred unique checks |
| Migrations | Scalar defaults, index changes, validation-only revisions, explicit record transforms, resume after interruption, model-scoped access | Root identity/layout changes; only one maintenance job at a time |
| M access | Read every node, write valid scalars | Writes that maintain indexes and references |

[Known limitations](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/known-limitations.md) lists current defects and their workarounds.
Read it before you build on this project.

## Try it

You need Python 3.12 or later and [uv](https://docs.astral.sh/uv/). The inventory example
runs in memory, so you do not need YottaDB:

```sh
git clone https://github.com/jabez007/you-have-mumps-friend
cd you-have-mumps-friend
uv sync
uv run python -m examples.inventory
```

To use YHMF in your own project, follow [get started](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/getting-started.md). To store data
durably, follow [set up a YottaDB database](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/guides/native-setup.md).

## Documentation

- [Get started](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/getting-started.md): a short tutorial.
- [Documentation map](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/README.md): every guide, reference, and design record.
- [Warehouse inventory example](https://github.com/jabez007/you-have-mumps-friend/blob/main/examples/inventory/README.md): a complete application.
- [Architecture](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/yottadb_hierarchical_mapper_architecture.md): goals and open questions.
- [Changelog](https://github.com/jabez007/you-have-mumps-friend/blob/main/CHANGELOG.md) and [contributing](https://github.com/jabez007/you-have-mumps-friend/blob/main/CONTRIBUTING.md).

## License

YHMF is licensed under [GPL-3.0-or-later](https://github.com/jabez007/you-have-mumps-friend/blob/main/LICENSE). The distribution is `mccann-hub-yhmf`,
imported as `mccann_hub.yhmf`.

YottaDB and YDBPython have separate upstream AGPLv3 notices. See the
[component licenses](https://github.com/jabez007/you-have-mumps-friend/blob/main/docs/licensing.md)
for their terms. This repository provides no legal advice or counsel-reviewed deployment guidance.
