# YottaDB Hierarchical Model Framework

**Architectural specification: v0.5**

**Date:** November 6, 2026

**Status:** Implemented through v0.5 with the limitations below

**License:** GPL-3.0-or-later

**Language:** Python; cross-language bindings deferred
**Project:** You Have MUMPS, Friend (YHMF)

The distribution is `mccann-hub-yhmf`, imported under `mccann_hub.yhmf`.
The [storage contract](storage-format.md) defines the prototype's concrete
encodings. The [documentation map](README.md) lists the guides for implemented
behavior, and [known limitations](known-limitations.md) lists what is missing.

## 1. Vision

Build a code-first, object-to-hierarchical-data mapper for YottaDB that combines the declarative developer experience of Odoo and Ferro with the direct, efficient hierarchical persistence model of MUMPS.

Developers should be able to define typed Python models with fields, validation, relationships, and indexes while retaining direct access to the underlying globals.

**The governing principle is that hierarchy is a first-class data-model primitive, not an implementation detail beneath a relational abstraction.**

## 2. Goals and non-goals

### Goals

1. Provide declarative Python models based on Pydantic v2.
2. Map model instances to native, inspectable YottaDB globals.
3. Support embedded objects, keyed child collections, and independent references.
4. Offer predictable CRUD and native transactional operations.
5. Define deterministic, versioned storage and key encodings.
6. Add secondary indexes, bounded queries, and explainable query plans.
7. Provide reviewable schema evolution and migrations.
8. Let plain M code read and write this data under a documented contract. This
   is the project's main difference from an ORM on a SQL database. See section 11.
9. Keep the persisted representation independent of Python to permit a future Go implementation.
10. Narrow the migration maintenance gate to the migrating model. See section 10.

### Non-goals

The framework will not initially recreate Odoo's record rules or application framework.

Computed fields and model inheritance are in scope, because the inventory application
needs them, but the framework will not copy their relational designs. Each one needs a
design that fits globals, such as embedding or delegation in place of table-per-class
inheritance.

The inventory example reuses its id declaration through ordinary Python field
inheritance, preserving each concrete global and owned path. Embedding and References
remain the choices for shared data with different lifecycles. Shared-global variants
need a concrete workflow and a separate storage design. See
[ADR 0007](adr/0007-compile-time-model-reuse.md).

It will not use SQL as its primary persistence layer, automatically distribute transactions, or hide inefficient global scans behind convenient query expressions.

It will not introduce a shared Python/Go runtime prematurely.

## 3. Licensing

The framework's original source code will use **GPL-3.0-or-later**, with SPDX identifiers and a canonical license file.

YottaDB and YDBPython publish AGPLv3 terms and separate upstream notices. The
[component license page](licensing.md) links those sources. The project has not
obtained counsel review and makes no deployment-specific representation about
combining these components, proprietary applications, or network services.

Runtime dependencies will undergo license compatibility review. Inspiration from Odoo and Ferro does not authorize copying implementation code without checking its provenance and license.

## 4. Developer model API

The implemented declarations use this API:

```python illustrative
from decimal import Decimal
from mccann_hub.yhmf import Model, Field, Children, ChildField, Reference

class Product(Model):
    __global__ = "^Product"

    id: str = Field(primary_key=True)
    sku: str = Field(unique=True)
    description: str

class Lot(Model):
    number: str = Field(primary_key=True)
    product: Reference[Product] = Field(index=True)
    quantity: Decimal = Field(
        default=Decimal("0"), ge=0
    )

class Bin(Model):
    code: str = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="number")

class Warehouse(Model):
    __global__ = "^Warehouse"

    id: int = Field(primary_key=True)
    name: str
    bins: Children[Bin] = ChildField(key="code")
```

The framework persists explicitly validated model objects.

Repository operations include `get`, `insert`, whole-aggregate `save`, `delete`,
scalar/reference `patch`, and keyed child insertion/deletion. Owned reads and
mutations use explicit tuple paths; automatic tracking and bound proxies are deferred.

Modifying a Pydantic object must not silently write to the database.

## 5. Relationship semantics

The framework distinguishes three relationship categories.

| Relationship | Semantics                                      |
| ------------ | ---------------------------------------------- |
| Embedded     | Parent-owned data without independent identity |
| Children     | Keyed records physically nested under a parent |
| Reference    | Link to an independently existing record       |

A child's identity includes its ancestor path and its own key.

Moving a child to a different parent therefore changes its physical identity. Applications requiring stable identity across moves should use top-level models and references.

Physical nesting should follow ownership, lifecycle, and access patterns rather than automatically mirroring every one-to-many relationship.

References enforce target existence on each source write. The target must be a
registered top-level model, and a non-null reference must have the target's key
type. The default and currently supported deletion policy is `restrict`, including
optional references. Removing the source or changing its reference releases the
target. Explicit `set_null` and cross-record cascades are deferred. Owned children
continue to follow the lifecycle of their physical parent.

Source writes, reverse-reference entries, and incoming counts share one native
transaction. The count provides a direct read for concurrent deletion checks.
This guarantee covers mapper writes; raw writes must preserve the same contract.

## 6. Canonical global storage

Layout one stores a record like this:

```text
^Warehouse(7,"%m","type")="Warehouse"
^Warehouse(7,"%m","schema")=1
^Warehouse(7,"name")="s:Main"
^Warehouse(7,"bins","s:A01","lots","s:L-001","product")="r:s:P42"
^Warehouse(7,"bins","s:A01","lots","s:L-001","quantity")="d:12.50"
```

The [storage contract](storage-format.md) defines these encodings. Type tags make
values self-describing, but M code must strip them before arithmetic, and tagged
index values do not sort in numeric order. Section 16 asks whether a later
layout should change this.

Framework metadata uses reserved field names. Application-defined names must not collide with reserved names.

The storage format will establish strict rules for:

- Canonical primary-key and child-key encoding.
- Distinguishing numeric keys from numeric-looking strings.
- Missing values, null, false, zero, and empty collections.
- Decimal precision and serialization.
- Global name ownership, key length, and path depth.
- Storage-layout versioning and backward compatibility.

The library will respect YottaDB's limits on subscript count, key size, and node value length.

No silent path truncation, lossy conversion, or implicit opaque-blob storage will be permitted.

A canonical fixture suite must prove that the format is readable independently of Python.

## 7. Internal architecture

```text
Application / domain services
          |
Declarative Pydantic models
          |
Model metadata and registry
          |
Repository / unit of work
          |
+----------------------------+
| Key/path codecs            |
| Query planner              |
| Secondary index manager    |
| Schema registry            |
| Transaction coordinator    |
+----------------------------+
          |
Native YDBPython adapter
          |
YottaDB globals / transactions
```

Core model metadata and path planning must remain independent of YDBPython.

The native adapter will have a deliberately small interface for reading, writing, deleting, traversing, and executing atomic operations.

Domain logic will not be embedded in the database adapter.

## 8. Transactions and consistency

All record writes and their corresponding index updates must occur within one native YottaDB transaction.

The public transaction API uses an explicit callback:

```python illustrative
from typing import cast

def adjust_stock(tx):
    at = ("bins", "A01", "lots", "L-001")
    lot = cast(Lot, tx.get(Warehouse, 7, at=at))
    tx.patch(Warehouse, 7, at=at, changes={"quantity": lot.quantity - Decimal("2")})

db.atomic(adjust_stock)
```

The [inventory application](../examples/inventory/README.md) demonstrates complete
receive, transfer, and adjustment commands with exact arithmetic and ledger entries.
Detached `save` remains last-writer-wins; read-modify-write safety requires reading
inside the callback. Native validation errors are revalidated through the final
stable TP attempt, as described in ADR 0005.

YottaDB can restart a transaction and execute its callback again. Therefore:

1. The framework must reconstruct transaction-local state for each attempt.
2. Application logic must not perform external side effects inside retryable callbacks.
3. Transaction restarts and rollbacks must propagate correctly.
4. Unique-index claims must be concurrency-safe.
5. External messaging should use a transactional outbox or equivalent design.

The initial Python implementation will use process-based parallelism for database-heavy concurrent work.

## 9. Indexing and querying

Secondary indexes are native global cross-references.

Example:

```text
^YHMFIndex("Warehouse","bins.lots.product",value_token,7,location_digest)=location_json
```

Value tokens and location digests use the encodings in the storage contract.
Entries preserve root identity and the concrete owned occurrence path.

Creating, modifying, or deleting an indexed value must update the index in the same transaction as the record.

Unique constraints must explicitly define null handling, key normalization, and contention behavior.

The query API supports direct keys, bounded hierarchical traversal, indexed equality,
explicit bounded scans, and query explanation:

```python illustrative
query = db.query(Warehouse).where(**{"bins.lots.product": Reference[Product](key="P42")})
print(query.explain())
```

A missing index must not silently cause an unbounded database scan. Applications may explicitly authorize a bounded scan when necessary.

Joins, full-text search, complex aggregates, and arbitrary SQL compatibility remain outside the initial scope.

## 10. Schema evolution

Python model declarations are the source of truth for intended schema.

The framework generates canonical schema snapshots identifying:

- Stable model and field identities.
- Storage locations and codecs.
- Relationship types.
- Index definitions.
- Schema and layout versions.

The database maintains its own record of the installed schema version.

The framework detects incompatible schemas and refuses unsafe access. Schema
changes require an explicit migration.

Schema comparisons and deterministic review plans are implemented. The initial
executor supports scalar literal-default backfills in existing owned trees, new scalar indexes,
and index promotion, uniqueness downgrade, or removal. Job-scoped reservations detect
duplicate values across validation batches before application data changes.
It uses a maintenance gate and bounded checkpoints before
atomic activation. The gate currently blocks every model in the database. Broader migration execution
remains a later milestone.

Narrowing the gate to the migrating model is a goal, not a hard requirement. Odoo's
[upgrade documentation](https://www.odoo.com/documentation/18.0/administration/upgrade.html)
says a production database upgrade renders the database unavailable while it runs, so a
database-wide gate is not clearly worse than that benchmark for a version upgrade. It is
a poor fit for adding one field or index to one model. See [#21](https://github.com/jabez007/you-have-mumps-friend/issues/21).

Production migrations must be reviewable, recoverable, and appropriately checkpointed.

Renaming a field or relocating a subtree must be treated as a physical data migration, not merely a metadata change.

## 11. Native access and interoperability

Direct access to YottaDB globals remains an explicit design goal.

Plain M code must be able to read and write this data. Reads work today. Writes
currently bypass validation, indexes, and constraints, as the
[M access guide](guides/m-access.md) shows. The write contract must say which of
these the framework can maintain for M writers, for example through triggers or a
generated M interface, and what validation means when Pydantic cannot run. See [#25](https://github.com/jabez007/you-have-mumps-friend/issues/25).

The framework will also provide a controlled raw-access interface for Python.

The storage format must also be usable by Go programs eventually.

The project will maintain golden storage fixtures and serialization contracts to make cross-language compatibility testable.

## 12. Operations and security

The framework provides schema inspection, bounded expected-index validation,
query explanations, and migration status. Full reverse index audits and repair
procedures remain later extensions.

Diagnostic output must avoid exposing sensitive values by default.

Framework-owned global names and paths must be validated, and applications must not be allowed to turn untrusted user input into arbitrary global write operations.

Backup, recovery, journaling, and replication remain primarily YottaDB responsibilities. Documentation will explain how the framework interacts with them.

## 13. Milestones

### v0.1 — Hierarchical persistence

Implement declarative models, registry, typed codecs, native paths, CRUD, embedded objects, child collections, and explicit transactions.

Acceptance requires real YottaDB integration tests, storage-format fixtures, rollback testing, and concurrent process tests.

### v0.2 — Indexes and queries

Implement references, equality and unique indexes, indexed queries, bounded iteration, query planning, and index consistency diagnostics.

Acceptance requires verified atomic index maintenance and concurrency-safe uniqueness.

### v0.3 — Schema evolution

Implement schema snapshots, schema comparisons, migration generation, migration execution, index rebuilds, and recovery procedures.

Acceptance requires interrupted-migration tests and schema compatibility tests.

Version 0.3.0 completes this milestone within the following boundary: schema inspection and comparisons,
deterministic review plans, scalar-default additions in existing owned trees,
scalar index additions, promotions, downgrades and removals, and explicit index
rebuilds for an unchanged installed declaration. Jobs use maintenance ownership,
bounded updates, atomic activation, safe early abort, and durable resume.
Qualification covers native restart, process-death, concurrency, legacy-job
compatibility, and installed-wheel tests. New owned containers, destructive
record changes, reference migrations, and full reverse-reference repair belong
to later milestones.

### v0.3.1 — Integrity hardening

Protect initial installation from unmanaged globals, verify installed snapshot
integrity, qualify fingerprint compatibility, and document detached-write
concurrency. Complete native qualification before starting v0.4. See
[implementation order](implementation-plan.md) and
[ADR 0001](adr/0001-schema-installation-integrity.md).

### v0.4 — Fine-grained hierarchical mutations

Add transactional scalar patches and keyed owned-child insertion, updates, and
deletion with direct node addressing. Preserve Pydantic validation, affected
indexes, unique claims, references, and whole-aggregate replacement. Verify native
concurrency and compare direct node mutations, framework mutations, and whole
replacements as unrelated hierarchy sizes grow.

Go interoperability, general query expansion, automatic change tracking, and
native performance extensions are deferred.

Version 0.4.0 implements this milestone with explicit ancestor-validation limits
and a bounded aggregate-size check for child insertion. Storage layout one and
whole-aggregate operations remain compatible. See
[ADR 0004](adr/0004-hierarchical-mutations.md) and the
[native performance report](performance.md).

### v0.5: Schema stability, reliability, and developer experience

Canonical schema format 2 is derived from the typed registry and explicit codecs,
independent of generated JSON Schema. Existing format-one snapshots and record
fingerprints remain intact. Transactional bindings verify legacy compatibility;
maintenance resumes exact saved manifests. Custom validation and callable default
changes require an owning application schema revision. See
[ADR 0006](adr/0006-canonical-schema-contracts.md).

The schema phase passed native qualification before adversarial state-machine and
recovery tests began. Those checks passed before the warehouse ledger implementation.
The example uses public APIs, independent identities, explicit ownership, and atomic
commands. Benchmarks compare equivalent partial and whole-replacement workflows;
direct node assignment is reported separately as a lower bound. See
[testing](testing.md), [the example](../examples/inventory/README.md), and
[inventory measurements](inventory-performance.md). Missing-index diagnostics list
usable equality paths. Indexed aggregate reads still materialize matching roots;
optimistic revisions, occurrence projections, and broader migrations remain deferred.

## 14. Testing strategy

Testing will combine pure unit tests with integration tests against an actual YottaDB installation.

Core coverage includes model metadata, codec round trips, typed values, nested paths, transaction conflicts, rollback, concurrent updates, unique constraints, index consistency, and storage limits.

Fault injection and property-based testing should be used where valuable.

Performance benchmarks must distinguish database-engine performance from mapper overhead.

## 15. Design decisions

**D-001:** Python first, with a language-independent storage format.

**D-002:** Native YottaDB globals are authoritative; SQL is optional interoperability.

**D-003:** Pydantic provides model declaration and validation, not automatic persistence.

**D-004:** Hierarchical ownership is explicit and affects identity.

**D-005:** Storage encodings are versioned and independently testable.

**D-006:** Transactions use retry-safe callbacks with explicit mutation boundaries.

**D-007:** Queries must have predictable, bounded execution plans.

**D-008:** Original framework code is GPL-3.0-or-later.

**D-009:** Ferro and Odoo are design references, not implementation dependencies.

**D-010:** Plain M code is a supported reader and writer of framework data.

**D-011:** Features borrowed from relational ORMs get hierarchy-native designs. The
framework does not bend globals to imitate tables.

**D-012:** A warehouse inventory application is the first proof of concept, and its
needs set the order of feature work. Odoo is the benchmark for what it must do.

## 16. Outstanding architectural questions

Before finalizing the public API, several questions need focused prototypes:

1. Should models extend Pydantic directly or use composition?
2. How should numeric and string keys be encoded while preserving native collation?
3. How are explicit null values, empty children, and presence markers represented?
4. What transaction API is safest with automatic restarts?
5. Should indexes be managed exclusively by the mapper, or can YottaDB triggers/AIM safely contribute?
6. Which YottaDB, Python, and Linux versions will CI officially support?
7. What additional metadata would a native Go implementation need?
8. What licensing guidance should the project provide for downstream applications?
9. What should a per-model maintenance gate do at its edges, such as references and
   multi-model transactions? See [#21](https://github.com/jabez007/you-have-mumps-friend/issues/21).
10. Should a later storage layout use native M values and ordered index subscripts,
    given the tradeoff against self-describing values? See [#24](https://github.com/jabez007/you-have-mumps-friend/issues/24).

## 17. Implementation status

The [changelog](../CHANGELOG.md) records what each release added, and
[known limitations](known-limitations.md) lists what is missing. The
[storage contract](storage-format.md) is authoritative for concrete encodings.

**The measure of success is whether developers gain an approachable model layer without losing the control, performance characteristics, and transparency of MUMPS globals.**

---

_Draft specification. The [documentation map](README.md) lists implemented behavior.
Later milestones and APIs beyond that behavior remain design targets._
