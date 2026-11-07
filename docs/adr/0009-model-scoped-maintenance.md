# ADR 0009: Scope ordinary maintenance checks to the affected model

Status: accepted. [CI run 42324520583](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42324520583)
passed both configured engines before issue 21 was closed.

## Existing mechanisms and problem

The maintenance gate names one durable job. Its checkpoint identifies the affected
global, and that global already carries a fingerprint barrier. Database schema
checks run inside the data transaction. Reference target validation uses those
same checks, while deletion uses transactional inbound counts. The previous global
gate unnecessarily prevented unrelated application work.

## Decision

Keep one maintenance job at a time. Ordinary access reads the gate, validates the
job checkpoint and barrier ownership, and blocks the affected global. Unrelated
models can use normal CRUD, indexes, and resolution. Corrupt or terminal active-job
metadata fails closed. No new persisted fields, scope caches, or lock protocol.

Reference behavior follows existing operation boundaries:

- Source reads and patches to unrelated scalar fields do not resolve targets.
- New or changed references must validate targets; a migrating target blocks them.
- Full saves validate every nonnull target, even when a reference did not change.
- Removing an incoming reference or deleting its source releases normal reverse edges.
- Deleting a target referenced by the migrating model remains restricted by its counts.
- A callback needing the gated model rolls back its staged writes, including writes
  to other globals. Ordinary failed-write poisoning remains unchanged.

An empty resolve batch accesses no model and returns an empty result. Explicit
adoption, a second migration, and a second rebuild remain serialized maintenance
operations. Index rebuilds use the same model-scoped data-access rule.

## Alternatives and tradeoffs

A connected-model gate would add declaration scanning and dependency state without
strengthening the existing target/count checks for supported migrations. Dynamic
per-model job locks would require collision, ordering, recovery, and older-client
rules. Neither is needed to allow unrelated CRUD under one job.

Unrelated operations read the mutable checkpoint to establish trusted scope. A batch
commit can cause a native transaction restart, and engine block conflicts remain
possible. This is permission to proceed, not a contention or throughput guarantee.
Measure before introducing a separate immutable scope marker.

Older clients that honor the global gate remain conservatively blocked. Older
fingerprint checks still reject the affected model. No schema or storage migration
is required. Raw writers remain outside mapper maintenance enforcement and must be
coordinated by the operator. Transform migrations must retain atomic reference
validation and count maintenance if their supported operations expand.

## Verification

Both-adapter tests cover unrelated CRUD/queries/resolution, reference edges, mixed
callbacks, corrupt jobs, second-job refusal, rebuilds, and legacy gate behavior.
A deterministic native test releases an unrelated writer and a migration worker
after both reach transaction barriers; the writer completes 100 indexed updates
while maintenance remains active. Existing interruption, restart, abort, and recovery
tests remain required. No native result is implied by the offline tests.

Generated M writers now follow this rule using a bounded reader of the existing
canonical flat checkpoint grammar. It validates fields, domains, ordering, supported
formats, active phase, identity, and the owning schema barrier. Unknown fields/formats
fail closed. No job metadata changes or process-local scope caches are introduced.
Earlier exported routines keep their conservative global gate behavior.
