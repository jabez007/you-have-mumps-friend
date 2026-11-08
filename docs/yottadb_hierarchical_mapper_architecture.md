# YottaDB Hierarchical Model Framework

**Architectural specification: v0.5**

**Date:** November 7, 2026

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

Computed fields and model inheritance are in scope, but the framework will not copy
their relational designs. Each one needs a design that fits globals, such as embedding
or delegation in place of table-per-class inheritance.

Ordinary Python field inheritance reuses declarations, such as a shared id field, and
keeps each concrete model's global and owned paths. Embedding and References
remain the choices for shared data with different lifecycles. Shared-global variants
need a concrete workflow and a separate storage design. See
[ADR 0007](adr/0007-compile-time-model-reuse.md).

It will not use SQL as its primary persistence layer, automatically distribute transactions, or hide inefficient global scans behind convenient query expressions.

It will not introduce a shared Python/Go runtime prematurely.

## 3. Licensing

The framework's original source code will use **GPL-3.0-or-later**, with SPDX identifiers and a canonical license file.

YottaDB and YDBPython publish AGPLv3 terms and separate upstream notices. The
[component license page](licensing.md) links those sources. The project has had no
legal review. It gives no guidance on combining these components, proprietary
applications, or network services. See
[#35](https://github.com/jabez007/you-have-mumps-friend/issues/35).

Runtime dependencies will undergo license compatibility review. Inspiration from Odoo and Ferro does not authorize copying implementation code without checking its provenance and license.

## 4. Developer model API

The implemented declarations use this API. These models are a cut-down version of the
[inventory example](../examples/inventory/README.md), the first proof of concept (see
D-012), and later sections reuse them as examples:

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
type. The default deletion policy is restrict, including optional references.
set_null validates and clears nullable fields on surviving owners; cascade deletes
the nearest keyed source owner. Embedded references belong to their nearest keyed
child or independent root. Discover the complete bounded closure before deciding
restrict conflicts; internal edges are removed together and cycles terminate through
native owner identities. Parent deletion dominates overlapping child deletions.
Owned descendants continue to follow their physical parent.

Policy deletes allow 100 distinct decoded owners, 1000 incoming edges, and 10,000
distinct decoded value nodes, failing with MutationError before writes if exceeded.
Exact source declarations must be registered. Null changes reuse ordinary patch
validation, including its custom-ancestor restrictions. Final collection lengths and
root budgets are checked before the union reference/index transition and physical
deletion. A source under maintenance conservatively blocks the policy delete with
ReferenceViolation. Policy-only changes use explicit reviewed validation migrations;
existing restrict snapshots and edge layouts remain unchanged. See ADR 0011.

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
values self-describing, and M code must strip them before arithmetic. Native semantic
probes qualified this reader contract on both supported engines. ADR 0012 retains
record layout one: untagged text still has the engine's arithmetic precision limits.
Ordered indexes have a separate explicit ordered-v1 codec and migration contract;
existing equality tokens do not sort in numeric order.

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

The [inventory application](../examples/inventory/README.md) uses this API for receive,
transfer, and adjustment commands with exact arithmetic and ledger entries. Aggregate
boundaries are the application's choice. [ADR 0017](adr/0017-inventory-bin-aggregate-boundary.md)
records how the example chose them: it roots each bin separately, so stock in one
warehouse does not count against a single root's node budget.
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
ordered range/prefix/directional reads, bounded root pages, explicit scans, and query explanation:

```python illustrative
query = db.query(Warehouse).where(**{"bins.lots.product": Reference[Product](key="P42")})
print(query.explain())
```

A missing index must not silently cause an unbounded database scan. Applications may explicitly authorize a bounded scan when necessary.

Ordered walks seek value subscripts directly, then root keys and concrete locations,
with 1000 combined value/occurrence visits per call. Occurrence cursors are scoped to
the installed schema, selection, and direction. Ordered root pages emit each root at
its canonical first matching occurrence, computed from its bounded aggregate. Their
query-scoped cursor advances past fully checked positions; empty progress pages
preserve the visit bound without unbounded deduplication history. all() retains strict
budget failure. Exactly-once pagination requires stable data across calls. Typed
field selectors feed the same planner. Equality cursors retain their formats. Up to eight declared
predicates use a candidate index and bounded witness probes under one shared visit
budget. Explain reports each predicate. Owned predicates match independently under
a root; combined occurrence projection is rejected rather than implying a join.

Joins, full-text search, complex aggregates, and arbitrary SQL compatibility remain outside the initial scope.

Foreign layouts can be read through explicit GlobalView/MappedField projections.
These use native paths and pure decoders, validate complete flat scalar models, and
never install metadata, indexes, or rewrite the source. They provide no managed
write or reference/ownership guarantees.

Initial adoption of larger exact-layout globals uses the existing maintenance
journal with an explicitly absent source schema and bounded validate/build/verify
passes. Only final activation publishes installed metadata; early abort restores
absence. No managed data format is fabricated for foreign globals.

Root or owned Decimal fields may declare a bounded owned sum. A shared secondary transition
combines validated quantity deltas per concrete target path and maintains totals and normal index
entries in one TP, without sibling reads. Root/ancestor custom validation, arbitrary
compute callbacks, reference dependencies, and sums of derived sums are rejected.
Whole writes derive totals; complete owner reads verify them. Source numeric domains
and decimal-sum-v1 dependencies are explicit schema contracts. The shared total is
an intentional contention point. See [ADR 0015](adr/0015-root-owned-stored-sums.md).

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
atomic activation. Ordinary access is scoped to the affected model; unrelated CRUD
continues while one maintenance job owns the coordinator. Explicit named transforms
reuse that lifecycle for reviewed record rewrites, including new ownership and
reference changes, with pinned outputs and a declared abort boundary. Root global,
key identity, and layout changes remain outside this executor. See
[ADR 0010](adr/0010-explicit-record-transforms.md).

Reference target checks and inbound counts preserve their normal boundaries during
maintenance. Full saves referencing the affected model can block even when a local
scalar patch would not. Cross-model callbacks roll back when they need an unavailable
model; concurrent maintenance jobs remain refused. Older clients can remain globally
blocked. [ADR 0009](adr/0009-model-scoped-maintenance.md) records the decision.

Production migrations must be reviewable, recoverable, and appropriately checkpointed.

Renaming a field or relocating a subtree must be treated as a physical data migration, not merely a metadata change.

## 11. Native access and interoperability

Direct access to YottaDB globals remains an explicit design goal.

Plain M reads use the documented native storage encodings. Managed M writes use
explicit schema-bound generated routines. A routine supports UTF-8 `str`, `int`,
`Decimal`, dates, UTC datetimes, finite choices, references, embedded objects, and
their nullable forms. It validates its input, then updates markers, counts, equality,
ordered, and unique indexes, reverse references, and stored sums in the same
transaction as the record.

Each routine owns its transaction or joins a restartable caller transaction. A
participant failure aborts the whole caller transaction. A routine rechecks a failure
status on a stable transaction attempt before it returns the status.

In keyed hierarchies, M routines patch root and owner scalars, create empty roots and
branches, and delete populated subtrees in bounded postorder. Python builds populated
trees and replaces whole hierarchies.

New exports check the exact installed metadata and maintenance scope. Python
validators are never silently omitted, because export fails for any declaration the
M contract cannot enforce. Direct SET/KILL bypass those guarantees. The
[M access guide](guides/m-access.md) describes supported operations and caller restart
rules. [ADR 0013](adr/0013-explicit-m-writer-contract.md) records the trigger comparison
and decisions. Application rules belong in the application's own M transaction, not
in automatic mutation hooks. The inventory example's receipt routine updates stock and
the ledger this way.

Python callers can resolve managed owner addresses with raw_path inside atomic.
Raw writes bypass mapper validation and secondary maintenance; see the
[raw-access contract](guides/m-access.md#resolve-a-managed-raw-address).

The storage format must also be usable by Go programs eventually.

The project will maintain golden storage fixtures and serialization contracts to make cross-language compatibility testable.

## 12. Operations and security

save preserves whole-aggregate last-writer-wins intent while applying physical node
differences. It audits existing physical nodes under the record budget, removes raw
extras, and rewrites desired descendants when deleting an extra parent value.
It still loads/validates full aggregates and maintains ordinary secondary entries;
this reduces write amplification, not read scope or detached lost updates.

Database.path selects owned models through typed Python field/key access, returning
declaration-bound ModelPath values. get infers the selected owner type and checks its
binding at runtime. Registry.reference infers a primary-key constructor while keeping
the existing Reference[Target] object and storage contract. No type-checker plugin or
query language is required. Tuple paths and explicit Reference construction remain
supported. Database.field builds typed query fields for equality predicates and for
order_by on declared ordered indexes. Range and prefix predicates still take string
field names. See [typed equality fields](guides/indexes-and-queries.md#typed-equality-fields).

Explicit patch_many groups prepare 1–100 scalar/reference owner changes using the
ordinary partial-validation policy, then apply the union of normal index/reference
transitions. This supports unique swaps without deferring constraints throughout
atomic callbacks. Duplicate owner requests are rejected; no new index layout or
stored transaction state is needed. See [grouped patches](guides/indexes-and-queries.md#exchange-unique-values).

The framework provides schema inspection, bounded expected-index validation,
query explanations, and migration status. Full reverse index audits and repair
procedures remain later extensions.

Diagnostic output must avoid exposing sensitive values by default.

Authorization belongs to the application's trusted service boundary for now.
Database, Store, and raw paths do not authenticate or authorize principals. Python
policies cannot constrain an independent M process with database access. Business
rules that an application enforces are not access control. [ADR 0008](adr/0008-application-authorization-boundary.md)
records the decision, deployment limits, and requirements for reconsidering scoped handles.

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
usable equality paths. Indexed aggregate reads still materialize matching roots.
Unreleased extensions add bounded occurrence projections, indexed predicate
intersections, directional root pages, named transforms, resumable adoption, and root/owned
stored sums. Optimistic revisions and general compute callbacks remain deferred;
see the [current limitations](known-limitations.md) for the implemented boundaries.

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

**D-008:** Original framework code, including generated M routines, is GPL-3.0-or-later.

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
8. Licensing guidance is resolved by [#35](https://github.com/jabez007/you-have-mumps-friend/issues/35):
   publish component license facts and no guidance on downstream deployments.
9. What should a per-model maintenance gate do at its edges, such as references and
   multi-model transactions? See [#21](https://github.com/jabez007/you-have-mumps-friend/issues/21).
10. Record-value choice is resolved by [ADR 0012](adr/0012-retain-tagged-record-values.md):
    retain tagged records and version ordered indexes independently. Qualify those
    index codecs and bounded query APIs under [#15](https://github.com/jabez007/you-have-mumps-friend/issues/15).

## 17. Implementation status

The [changelog](../CHANGELOG.md) records what each release added, and
[known limitations](known-limitations.md) lists what is missing. The
[storage contract](storage-format.md) is authoritative for concrete encodings.

**The measure of success is whether developers gain an approachable model layer without losing the control, performance characteristics, and transparency of MUMPS globals.**

---

_Draft specification. The [documentation map](README.md) lists implemented behavior.
Later milestones and APIs beyond that behavior remain design targets._
