# Changelog

This file records what each release added and what it left out.

## Unreleased

- Adds native insertion diagnostics attributing independent-parent retries to shared
  data blocks and shared-parent retries to the count node. Reports both engines and
  journaling modes, retaining current count and layout guarantees.

- Retains tagged record layout one after native M arithmetic/collation probes on
  both qualified engines and journaling modes. Adds a golden-tested M payload reader
  and records the separate versioning decision for future ordered indexes.

- Adds bounded transactional set_null/cascade reference policies, nearest-keyed-owner
  cascades, cycle handling, union index/reference maintenance, and final collection
  validation. Policy-only changes require a reviewed explicit metadata migration.
  Oversized deletion closures fail before writes.

- Adds bounded scalar lists as native numbered nodes, preserving null/empty values,
  whole-list equality/unique indexes, minimal patch writes, and shared root budgets.
  New list fields and item type changes use explicit record transforms initially.

- Fixes schema inspection of nullable Decimal alternatives, allowing revision-only
  migrations without changing snapshots, stored values, or indexes.

- Adds homogeneous str/int/bool Enum and Literal fields with canonical choice
  domains, primitive node encodings, strict stored-value restoration, and indexes.
  Enum domain changes require an explicit transform; literal defaults use the
  existing migration executor.

- Adds UUID scalar fields, versioned canonical metadata, equality/unique indexes,
  and literal defaults. Primary-key codecs remain str/int.

- Moves the inventory example's ledger to UTC datetime timestamps at application
  schema revision two, with frozen v1 declarations and an explicit resumable
  conversion. ISO string inputs remain accepted; returned timestamps are datetime.

- Adds versioned date and UTC datetime field codecs, canonical defaults/bounds,
  equality/unique indexes, and literal-default migrations. Existing scalar contracts
  remain unchanged; temporal type changes require an explicit migration.

- Rejects registered root/owned global-name or revision mutation before access,
  preventing new records from mixing stale schema snapshots with changed metadata.

- Adds explicit named record transforms to the existing migration lifecycle, with
  pinned source/output digests, final uniqueness reservations, reference maintenance,
  and a reviewed abort boundary. Root identity and layout changes remain blocked.

- Allows unrelated model operations during migrations and rebuilds, preserving
  target validation, restrict counts, one-job coordination, and legacy barriers.

- Keeps whole-value save semantics while writing only physical node differences.
  A bounded physical audit removes raw extras and can add read/traversal cost.
  Detached saves remain last-writer-wins; use explicit mutations for targeted changes.

- Adds Database.path selectors and ModelPath for owned reads with inferred result
  types. Keeps tuple paths and existing schema/storage identities.
- Adds Registry.reference typed factories inferred from a selected primary key,
  producing the existing Reference[Target] values without a generic-arity change.

- Adds Patch and patch_many for explicit groups of 1–100 scalar/reference owner
  changes, including unique-value swaps. Constraints remain immediate outside the group.

- Permits Pydantic virtual computed fields without persisting them or changing
  existing schema identities. They cannot declare mapper storage or index metadata.
- Adds an uncached total to the inventory warehouse example. Stored rollups remain
  deferred pending the independent M-writer contract.

## 0.5.0

- Adds canonical schema format 2, compatible legacy bindings, generated mutation
  and integrity testing, and a runnable inventory ledger.
- Keeps existing layout-one records, CRUD signatures, and legacy installed
  identities.
- Requires an explicit application schema revision for custom validation changes.
- Lists usable equality paths in missing-index errors.

See the [0.5.0 delivery report](docs/releases/0.5.0.md), the
[schema compatibility policy](docs/schema-compatibility.md), the
[inventory example](examples/inventory/README.md), and the
[native inventory measurements](docs/inventory-performance.md).

## 0.4.0

- Adds scalar and reference patches, owned-path reads, keyed child insertion and
  deletion, and atomic composition.
- Keeps layout one, whole-aggregate `save`, schema migrations, and existing index
  and reference identities.
- Rejects partial mutation under unsafe ancestor validation.
- Keeps a bounded aggregate size check for child insertion.
- Defers optimistic revisions, automatic change tracking, and Go bindings.

Native qualification covers deterministic process races, forced restarts, rollback,
index and reference checks, legacy migration compatibility, and the installed-wheel
workflow. The [native performance report](docs/performance.md) shows constant patch
node work as unrelated lot counts grow. It also shows material framework overhead
compared with direct native assignment.

## 0.3.1

- Rejects implicit installation over any unmanaged global data.
- Checks both installed metadata nodes during CRUD.
- Adds bounded explicit layout-one adoption.
- Keeps schema identities and layout version one.
- Confirms and documents legacy fingerprint drift across Pydantic versions.
- Keeps detached `save` as last-writer-wins. `atomic` protects a
  read-modify-write when the read is inside the callback.
- Defers optimistic record revisions beyond 0.4.

## 0.3.0

This release completes the schema-evolution milestone within the following scope.

- Adds schema inspection, schema comparison, and deterministic review plans.
- Adds scalar-default additions in existing owned trees.
- Adds scalar index additions, promotions, downgrades, and removals.
- Adds explicit declared-index rebuilds.
- Adds maintenance jobs with bounded updates, atomic activation, safe early abort,
  and interruption and resume.
- Keeps storage layout version 1 and legacy format-one schema jobs.
- Leaves new owned containers, field deletion or relocation, storage-type changes,
  reference migrations, and default-factory backfills for later work.

Qualification covers the deterministic checks, the installed-wheel schema migration
and index reconstruction workflow, and native rollback, restart, process-death, and
concurrency suites. The native CI matrix uses Python 3.12 with YottaDB r2.02 on
Ubuntu 22.04 and r2.06 on Ubuntu 24.04. These results apply to those tested
environments, not every accepted runtime or dependency version.
