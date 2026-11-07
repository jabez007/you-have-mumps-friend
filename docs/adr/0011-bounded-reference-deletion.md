# ADR 0011: Plan a bounded reference-deletion closure

Status: accepted. [CI run 42334391163](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42334391163)
passed both configured YottaDB versions, both journaling modes, and native stress
before issue 13 was closed.

## Existing mechanisms and problem

Reference fields already maintain exact reverse locations, inbound counts, secondary
entries, and unique claims in native transactions. Occurrence decoding checks native
addresses against registered owners and stored field bytes. Prepared patches retain
Pydantic validation, and ownership helpers check collection lengths. Restrict-only
deletion could remove a self-reference but could not clear optional sources or
delete a cyclic group of referencing owners safely.

## Decision

Keep restrict as the default. Add set_null for nullable References and cascade for
the nearest keyed source owner. A reference in an embedded value belongs to its
nearest keyed child or independent root. Cascades follow incoming references;
ordinary outgoing references keep their independent target lifecycles.

Discover the complete closure before choosing restrict conflicts or preparing null
changes. Parent removal dominates overlapping owned removals. Native path identity
terminates cycles. Restrict edges wholly inside the closure are released together;
surviving sources still restrict deletion. Group null changes by surviving owner and
use normal patch validation. Final collection lengths and net root budgets are
checked after closure normalization, avoiding invalid intermediate counts.

Each attempt permits 100 distinct decoded owners, 1000 incoming edges across the
closure, and 10,000 distinct decoded value nodes, including the target. Read reverse
subtrees with explicit physical address budgets and verify complete edge counts.
Cache decoded owners only within this attempt. Bounds raise MutationError before
writes. Malformed edges/counts fail safely. Every incoming source requires its exact
registered Python declaration; snapshots cannot replace application validators.
A source under maintenance conservatively blocks deletion with ReferenceViolation,
preserving earlier restrict behavior without interpreting a gated policy.

Apply the union of removed links and index claims using existing replace helpers.
Validate new targets and prevent surviving validator output from retaining a deleted
target. Keep the final zero-inbound-count assertion. Apply surviving field patches,
physical owner deletes, and collection counts in the same native transaction.
Rollbacks and restarts discard the whole plan; no checkpoint or background cascade.

## Compatibility

Policies appear in the existing schema references array. Default restrict snapshots,
record layouts, and reverse-edge identities stay unchanged. A policy-only change is
an explicitly reviewed validation/retag migration, with a review_delete_policy step.
It does not rewrite application fields or indexes. Target retargeting keeps its
existing manual/transform requirements. Older writers reject changed source
fingerprints or continue conservatively restricting target deletion.

## Alternatives and tradeoffs

Sequential ordinary deletes cannot handle internal restrict cycles and expose
temporary collection/claim states to later operations. Inferring policies from
snapshots would omit Python validators. Unbounded recursive deletion and resumable
cascades introduce large transactions or externally visible intermediate states.
The bounded closure reuses existing integrity modules and fails before changing data
when the application needs a larger explicit unlinking workflow.

Policy deletion reads and validates source owners, so it costs more than an O(1)
restrict check. Arbitrary custom ancestor validation retains existing partial-write
restrictions. A native block conflict can restart the entire attempt. Raw missing
edges cannot be discovered without scanning source globals; this contract covers
supported writers maintaining the documented reverse structures.

## Verification

Both-adapter tests cover nested owners, mixed-policy and set_null cycles, internal
and external restrict edges, overlapping closures, final lengths, all three limits,
null-rejecting/retaining validators, registered-source guards, edge counts, rollback,
write poisoning, unchanged policy-migration bytes, and schema goldens. Native tests
force a restart and coordinate independent delete, link, source-delete, and child
unlink transactions through explicit process barriers.
