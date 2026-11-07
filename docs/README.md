# Documentation

New here? Start with [get started](getting-started.md), then read
[known limitations](known-limitations.md).

## Learn

- [Get started](getting-started.md): store, change, and query a warehouse in memory.
- [Warehouse inventory example](../examples/inventory/README.md): a complete application
  with receive, move, adjust, stock lookup, and a movement ledger.

## Do a task

- [Define models and owned data](guides/models-and-ownership.md)
- [Use transactions](guides/transactions.md)
- [Change part of an aggregate](guides/partial-updates.md)
- [Use virtual fields and stored sums](guides/computed-fields.md)
- [Link independent records](guides/references.md)
- [Index and query records](guides/indexes-and-queries.md)
- [Change a schema](guides/migrations.md)
- [Transform records explicitly](guides/record-transforms.md)
- [Rebuild indexes](guides/index-rebuilds.md)
- [Adopt an existing global](guides/adoption.md)
- [Set up a YottaDB database](guides/native-setup.md)
- [Read and write the data from M](guides/m-access.md)

## Look something up

- [Limits](reference/limits.md)
- [Known limitations](known-limitations.md)
- [Storage format](storage-format.md): the byte contract for keys, values, and metadata.
- [Schema compatibility](schema-compatibility.md): when a schema change is compatible.
- [Component licenses](licensing.md): the license of each component, with links to its terms.
- [Changelog](../CHANGELOG.md)

## Understand the design

- [Architecture](yottadb_hierarchical_mapper_architecture.md): the goals, the decisions, and
  the open questions.
- Decision records:
  - [0001 Schema installation integrity](adr/0001-schema-installation-integrity.md)
  - [0002 Schema fingerprint compatibility](adr/0002-schema-fingerprint-compatibility.md)
  - [0003 Detached write concurrency](adr/0003-detached-write-concurrency.md)
  - [0004 Hierarchical mutations](adr/0004-hierarchical-mutations.md)
  - [0005 Native validation restarts](adr/0005-native-validation-restarts.md)
  - [0006 Canonical schema contracts](adr/0006-canonical-schema-contracts.md)
  - [0007 Compile-time model reuse](adr/0007-compile-time-model-reuse.md)
  - [0008 Application authorization boundary](adr/0008-application-authorization-boundary.md)
  - [0009 Model-scoped maintenance](adr/0009-model-scoped-maintenance.md)
  - [0010 Explicit record transforms](adr/0010-explicit-record-transforms.md)
  - [0011 Bounded reference deletion](adr/0011-bounded-reference-deletion.md)
  - [0012 Tagged records and independent ordered indexes](adr/0012-retain-tagged-record-values.md)

## Check the evidence

- [Correctness testing](testing.md)
- [Native performance](performance.md) and [inventory performance](inventory-performance.md)
- [Benchmark data](benchmarks/)
- [0.5.0 delivery report](releases/0.5.0.md)
- [Implementation plan](implementation-plan.md): a historical log of the 0.3.1 to 0.5 work.

## Contribute

- [Contributing](../CONTRIBUTING.md): run the checks, native tests, and benchmarks.
