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


## Review hardening: native control signals and nesting

Issues 1 and 7 exposed two gaps outside the validation-error policy. Native get,
set, delete, and traversal calls now retain the first restart or rollback signal
in a context shared by NativeStore instances during that attempt. Subsequent calls
propagate it without invoking the engine. Callback exit must also propagate it,
even when a caller caught it or raised a different error afterward. The context
resets on every exit and every restart. Native NodeEnd traversal termination
retains its ordinary behavior.

The same active context rejects nested native atomic entry through other handles.
MemoryStore rejects reentrant entry while holding its existing lock, which still
serializes concurrent threads and permits independent memory databases. This adds
no stored metadata or new retry counter. Database handle expiration and failed-write
protection remain in place. The validation-error policy above is unchanged.

This tracking covers native calls through the adapter. Raw yottadb-module calls
remain responsible for returning native control signals themselves. The supported
native concurrency model remains independent processes. Arbitrary callback side
effects cannot be automatically detected; issue 33 must preserve this contract.

Regressions exercise caught signals from all four storage methods, signal identity,
pending calls through another store, later callback errors, fresh transactions,
and outer/alias nesting. Four-process native tests run both plain and broad-handler
counter callbacks for 1,500 commits per worker. Controlled native tests also force
caught restart and rollback signals after a staged write.

## Issue 33 decision: retain stable-error retries

Retain the adapter policy. A callback can write through raw YDBPython calls or M
call-ins, including from a validator. No observed adapter writes does not prove a
transaction is read-only. Returning an error sentinel to reach commit-time read
validation would commit those writes. The native regression in test_retry_safety.py
demonstrates this failure mode and verifies that the existing policy rolls them back.
The migration auto-abort regression also exposed this defect in a framework path;
restoration now happens only after rollback, in a separate checkpoint-pinned TP.

An error-only fast path would need an engine-supported validate-and-abort operation,
or a controlled operation whose lack of writes can actually be established. Arbitrary
Python callbacks do not meet that condition. Instrumenting only Store is insufficient.
Do not add a process-local counter, callback classifier, or generic side-effect detector.

Applications can test a **known boundary** with unittest.mock.Mock configured to raise
AssertionError when called inside a callback. The transaction guide demonstrates this
without sending a message. This catches that boundary, not file writes, network calls,
closures, or other arbitrary behavior. Actual publication still belongs after commit;
reliable publication across process crashes requires an application-specific durable
delivery design beyond this issue. The native stress suite remains the correctness
gate. No attempt-count reduction or performance improvement is claimed.
