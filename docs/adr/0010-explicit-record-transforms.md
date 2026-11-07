# ADR 0010: Pin named record transforms within maintenance jobs

Status: implemented; native qualification required before closing issue 20.

## Existing mechanisms and problem

Literal-default migrations intentionally preserve existing bytes. Index rebuilds
already provide custom operation fingerprints, bounded clearing, reservations, and
recoverable phases. Maintenance provides exact saved snapshots, counters, abort,
retention, and activation. Applications need reviewed rewrites without replacing
that lifecycle or silently trusting arbitrary migration code.

## Decision

Add Transform(name, convert) to the existing plan/begin/advance API. A pure function
maps a source model to its exact target model. Preserve root global and key identity;
reject unknown layouts/shapes and schema downgrades. Operator-owned names version
the behavior; never hash function bodies or import stored code identifiers.

Validation pins each source/target field-byte digest under a hashed key address,
with the complete key in its payload. Digests are length-delimited SHA-256 over
sorted relative native paths and exact values. Operation format one explicitly
names digest version one. Backfill must reproduce the pinned source and output.
This detects changed inputs and nondeterministic output; it cannot prove purity.

Clear the affected model's indexes in bounded batches after validating/reserving
all final unique values. This permits swaps and avoids transient old/new claim
conflicts. Rewrite each record and its reverse edges/counts atomically, preserving
source record metadata until verification and retagging. Source reference path
bindings are compiled independently from the current target registry. Runtime
Python target classes do not participate in native reverse-edge equality.

Abort ends at the first committed index deletion or record rewrite, as stated in
the review plan. Later failures retain maintenance and require resume or backup
restore. Existing counters/phases, cleanup progress, pruning, snapshot integrity,
stable-error retries, and atomic activation remain authoritative.

## Alternatives and tradeoffs

Staging complete transformed aggregates would duplicate data and add prefixes to
paths already allowed at native depth/key limits. Rewriting fields without a
validation pass could discover uniqueness or nondeterminism only after committed
changes. Releasing claims record by record would prevent otherwise valid swaps.
The chosen approach adds digest metadata and rebuilding work, but keeps application
data in native globals and exposes the recovery boundary before execution.

This is an opt-in executor using the existing custom-operation seam, as index
rebuilds do. Ordinary schema plans and storage stay unchanged. Supported legacy
snapshots can be sources. Older executors refuse the operation marker. No database
conversion occurs on open, and no automatic undo, cross-global identity move,
arbitrary side-effect detection, or migration DSL is introduced.

## Evidence

Both-adapter regressions cover all requested transformation categories at every
phase, rollback, output changes, references, unique swaps, legacy source metadata,
digest corruption, and pruning. A committed golden fixes the operation/digest
contract. Native tests force TP restart and retain a stale checkpoint while another
process rewrites a source record. CI on both configured engines is required; offline
success alone does not qualify native behavior.
