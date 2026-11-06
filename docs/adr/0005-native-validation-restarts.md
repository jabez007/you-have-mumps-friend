# ADR 0005: Revalidate native application failures before exposing them

Status: accepted for v0.4; applies to CRUD and maintenance transactions.

## Evidence and problem

[CI run 42260567341](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42260567341)
failed the existing concurrent nested-index rebuild test on YottaDB r2.06.
A worker retained its old checkpoint and then saw entries published by its
competitor. Index validation raised `StorageError` before TP reached commit,
where the native engine would otherwise validate the read set and restart.

YottaDB's [transaction processing documentation](https://docs.yottadb.com/ProgrammersGuide/langfeat.html#transaction-processing)
describes commit-time read-set validation and the final critical-section attempt.
Its [TRESTART documentation](https://docs.yottadb.com/ProgrammersGuide/commands.html#trestart)
specifies the transition after three restarts. Native callbacks must account for
application errors observed before commit-time conflict detection. Python local
state is already fresh for each `Database.atomic` attempt, so changing the rebuild
cursor or accepting preexisting index entries would weaken the wrong invariant.

## Decision

In `NativeStore.atomic`, mapper errors (`YHMFError`) and value validation failures
(`ValueError`, including Pydantic validation errors) request native TP restart
while `$trestart < 3`. If the failure persists on the native final attempt, expose
the original exception and roll back. No failure is converted into a successful
commit. There is no stored retry counter, new lock protocol, or layout change.

Native restart/rollback/error signals pass through unchanged. Intentional
`RuntimeError` aborts, system exceptions, and interrupts are not reclassified.
Successful callbacks retain their existing fast path, with no extra intrinsic
read. The in-memory adapter remains serialized and requires no native revalidation.

This belongs at the adapter seam: the same mixed-read failure can affect schema
checks, model validation, references, uniqueness, and maintenance. Retrying only
one rebuild helper would leave the underlying condition elsewhere. The bounded
native policy preserves rejection of genuine corruption, rather than treating
unexpected entries as idempotent success or looping indefinitely.

## Contract and verification

Native callbacks can run again even when the operation ultimately reports a
validation failure. They must already be restart-safe and avoid external side
effects. Failed attempts expire their handles and discard all staged writes.

A focused adapter regression first failed for transient integrity errors and
persistent-error attempt counts, then passed with the policy. A real native
process regression makes both workers retain the same rebuild checkpoint,
finishes one worker completely, and only then releases the stale competitor.
A native persistent-error regression checks attempts 0 through 3 and verifies
that initial schema installation and record writes all roll back. Existing
simultaneous-worker tests remain unchanged. Native CI for both configured engines
is the acceptance gate; the adapter harness alone cannot establish engine behavior.
