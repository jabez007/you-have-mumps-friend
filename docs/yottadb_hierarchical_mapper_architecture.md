# YottaDB Hierarchical Model Framework

**Architectural Specification — Draft v0.1**

**Date:** November 3, 2026

**Status:** Prototype in progress

**License:** GPL-3.0-or-later

**Language:** Python first, Go interoperability planned
**Working name:** YottaDB Hierarchical Model Framework (YHMF)

The distribution is `mccann-hub-yhmf`, imported under `mccann_hub.yhmf`.
The [storage contract](storage-format.md) defines the prototype's concrete
encodings. Examples below remain conceptual unless the README marks them implemented.

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
8. Preserve native M and YottaDB interoperability.
9. Keep the persisted representation independent of Python to permit a future Go implementation.

### Non-goals

The framework will not initially recreate Odoo's entire ORM, object inheritance system, computed fields, record rules, or application framework.

It will not use SQL as its primary persistence layer, automatically distribute transactions, or hide inefficient global scans behind convenient query expressions.

It will not introduce a shared Python/Go runtime prematurely.

## 3. Licensing

The framework's original source code will use **GPL-3.0-or-later**, with SPDX identifiers and a canonical license file.

YottaDB and YDBPython are AGPLv3. GPLv3 §13 permits combining GPLv3 and AGPLv3 modules, but the AGPL-covered components retain their own terms. The framework will document those obligations and obtain legal clarification before making representations about proprietary applications or network-service deployment.

Runtime dependencies will undergo license compatibility review. Inspiration from Odoo and Ferro does not authorize copying implementation code without checking its provenance and license.

## 4. Developer model API

The following API is conceptual:

```python
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

The framework will favor explicitly persisted, validated model objects.

Repository operations such as `db.get()`, `db.insert()`, `db.save()`, and `db.delete()` will be the initial foundation. Optional bound record proxies can later provide convenient hierarchical navigation.

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

A proposed human-readable layout is:

```text
^Warehouse(7,"%m","type")="warehouse"
^Warehouse(7,"%m","schema")=1

^Warehouse(7,"name")="Main"

^Warehouse(7,"bins","A01","lots","L-001","product")="P42"
^Warehouse(7,"bins","A01","lots","L-001","quantity")="12.50"
```

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

The initial public transaction API will use an explicit callback:

```python
def move_stock(tx):
    lot = tx.get(
        Lot,
        parent=(Warehouse, 7, "A01"),
        key="L-001",
    )
    tx.save(
        lot.model_copy(
            update={"quantity": lot.quantity - Decimal("2")}
        )
    )

db.atomic(move_stock)
```

This API is illustrative.

YottaDB can restart a transaction and execute its callback again. Therefore:

1. The framework must reconstruct transaction-local state for each attempt.
2. Application logic must not perform external side effects inside retryable callbacks.
3. Transaction restarts and rollbacks must propagate correctly.
4. Unique-index claims must be concurrency-safe.
5. External messaging should use a transactional outbox or equivalent design.

The initial Python implementation will use process-based parallelism for database-heavy concurrent work.

## 9. Indexing and querying

Secondary indexes will be represented as native global cross-references.

Example:

```text
^YHMFIndex("lot","product","P42",7,"A01","L-001")=""
```

Index entries must contain sufficient information to resolve the full identity of a record.

Creating, modifying, or deleting an indexed value must update the index in the same transaction as the record.

Unique constraints must explicitly define null handling, key normalization, and contention behavior.

The query API will initially support direct-key lookup and bounded hierarchical traversal.

Later releases will support equality-index queries and a query explanation mechanism:

```python
query = db.query(Lot).where(product="P42")
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
and promotion of equality indexes to unique. Job-scoped reservations detect
duplicate values across validation batches before application data changes.
It uses a maintenance gate and bounded checkpoints before
atomic activation. Broader migration execution remains a later milestone.

Production migrations must be reviewable, recoverable, and appropriately checkpointed.

Renaming a field or relocating a subtree must be treated as a physical data migration, not merely a metadata change.

## 11. Native access and interoperability

Direct access to YottaDB globals remains an explicit design goal.

The framework will provide a controlled raw-access interface, but writes outside the mapper may bypass validation, indexes, and constraints.

Such modifications require documented consistency procedures.

The storage format must also be usable by native M routines and, eventually, Go programs.

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

### v0.4 — Developer experience and interoperability

Refine type support, diagnostics, performance, documentation, and tooling.

Prototype an independent Go reader/writer using the versioned global layout.

Benchmark the mapper against direct YDBPython access and identify abstraction overhead before considering a Rust or native performance layer.

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

## 17. Immediate implementation direction

The repository now implements the storage contract and v0.1 model operations,
plus a v0.2 prototype for references, indexes, and queries,
described in the [README](../README.md). The public API is `mccann_hub.yhmf`.
Owned children currently persist through their parent aggregate. The parent
controls their lifecycle; direct child CRUD is a later extension. The test suite
includes native rollback, forced restart, and process concurrency qualification.
The [storage contract](storage-format.md) is authoritative for concrete encodings.
Enforced references, reverse indexes, equality and unique indexes, and bounded
query plans are implemented. Queries currently target owning roots; nested-field
indexes can select those roots. Diagnostics audit expected secondary entries and
claims by bounded record pages. A full reverse index audit, direct child queries,
broader migrations, and Go interoperability remain later work.

Read-only schema inspection now exposes installed and declared snapshots,
including mismatched declarations, without changing schema or application data.
Semantic snapshot comparisons and deterministic migration review plans are
implemented, including nested fields, identity, relationships, and index
definitions. Planning is read-only and does not inspect record values or prove
data compatibility. An additive migration executor now supports scalar
literal defaults, index additions, and promotion of equality indexes to unique
at roots and inside existing embedded objects and child collections. Null owners
remain null, and child identities and collection counts are preserved. Each
parent tree commits atomically, and progress is checkpointed by parent. A database-wide
maintenance gate, source fingerprint barrier, bounded transactional checkpoints,
four validation/backfill/verification/metadata passes, and atomic activation
keep partial results inaccessible. Failed batches resume from their preceding
checkpoint. Abort is available only before the first backfill commits. New owned
containers, destructive, reference, and uniqueness-downgrade migrations remain
later work.

Begin with a storage-contract prototype rather than a full ORM.

Implement deterministic key encodings, native reads and writes, and golden fixture tests first. Then add the Pydantic model registry and explicit repository operations.

Validate the transaction restart behavior and process-level concurrency model before implementing secondary indexes and a broader query language.

**The measure of success is whether developers gain an approachable model layer without losing the control, performance characteristics, and transparency of MUMPS globals.**

---

_Draft specification. The README identifies implemented behavior. Later milestones
and APIs beyond that behavior remain design targets._
