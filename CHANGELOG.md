# Changelog

This file records what each release added and what it left out.

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
