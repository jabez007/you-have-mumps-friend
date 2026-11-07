# ADR 0013: Schema-bound explicit M writers

Status: accepted after [native prototype CI 42339948201](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42339948201);
generated writer implementation in progress.

## Existing mechanisms and experiments

Layout-one scalar tags, equality entry locations/digests, unique claims, schema
snapshots, maintenance gates, and native transactions already define a writer's
obligations. Raw SET/KILL bypass them. Python custom validation cannot be translated
reliably into M. Stored rollups must not promise correctness for unsupported writers.

The native prototypes in tests/m/YHMFWPROBE.m compare leaf SET/KILL triggers with an
explicit complete-input M routine. Both engines and both journaling modes passed: trigger
index changes roll back with the field; a leaf KILL maintains its toy index; parent
KILL does not invoke the leaf trigger and leaves a stale entry. The creation trigger
runs before later fields exist. These are semantic observations, not latency data.
YottaDB documents these [trigger semantics](https://docs.yottadb.com/ProgrammersGuide/triggers.html).
A broader trigger system could cover ancestor deletes and creation protocols, but
would need the mapper's schema, migration, validation, index, and ownership rules
plus a context protocol to cooperate with Python writes. The toy trigger is not a
complete managed-record implementation.

## Decision and initial supported contract

Generate an explicit, schema-bound M routine. Exporting source is read-only and
never installs triggers or metadata. Each insert/replace/delete owns one native TP;
nested transactions are rejected. Inputs are complete named field values, not a
patch or a Python-default invocation. Recompute database-derived state after every
restart. Check exact installed fingerprint/snapshot and record metadata on each
attempt; a stale routine fails rather than interpreting another schema. Initial
installation requires a genuinely empty global, in the same TP as the first insert.
A failed insert leaves data, metadata, indexes, and claims unchanged.

Start with flat non-null str/int declarations, ordinary equality/unique indexes,
ASCII string values, canonical signed integers of at most 18 digits, and supported
length/numeric bounds. Reject unsupported declarations at export and unsupported
values before writes. This is a safe subset of Python's value domain; it does not
restrict Python to ASCII or 18-digit scalar integers. Ignore no custom validation:
models with custom validation or unsupported constraints/normalization are rejected.
Defaults are not filled; callers supply all fields. Unknown fields and descendants
under a scalar input fail. Preserve existing layout/metadata/index bytes exactly.

Updates validate the existing record and its expected index/claim identities before
releasing/reclaiming changed entries. Delete conservatively refuses any inbound
reference namespace; it does not execute Python cascade/set_null policies. A
maintenance gate blocks M writes conservatively even for unrelated models. Unexpected
native errors roll back the owned TP and propagate to the caller's handler.

Owned trees, references as fields, ordered indexes, scalar lists, stored rollups,
custom validators, nullable values, non-ASCII input, and multi-record M transactions
remain unsupported by this initial generator. Thus supported M writes cannot change
rollup inputs. A future extension must implement the complete affected invariants
before accepting those models.

## Alternatives and tradeoffs

Installing triggers for all raw writes could provide broader writer coverage, but
introduces a second invariant-maintenance execution path and migration lifecycle.
Raw writes plus later rebuilding cannot preserve atomic uniqueness or references.
An explicit M API makes supported operations auditable and testable and requires
regeneration after schema changes. It cannot protect against raw writes or another
mutating trigger. Applications must route supported writes through the API and
coordinate other writers; this is not an authorization boundary.
