# ADR 0013: Schema-bound explicit M writers

Status: accepted after [native prototype CI 42339948201](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42339948201)
and [generated-writer CI 42341009542](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42341009542).

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
nested calls to these transaction-owning entry points are rejected. Inputs are complete named field values, not a
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
releasing/reclaiming changed entries. Delete conservatively refuses active inbound
references; it does not execute Python cascade/set_null policies. Newly
exported routines validate both existing checkpoint formats and barrier ownership
inside the write transaction, blocking only the affected model. Unknown/corrupt
state blocks all writes. Earlier exported routines keep the global block until
regenerated. Unexpected native errors roll back the owned TP and propagate to the
caller's handler.

Owned trees, ordered indexes, scalar lists, stored rollups,
custom validators, nullable values, and non-ASCII input remain unsupported by this
initial generator. Thus supported M writes cannot change rollup inputs. A future extension must implement the complete affected invariants
before accepting those models.

## Alternatives and tradeoffs

Installing triggers for all raw writes could provide broader writer coverage, but
introduces a second invariant-maintenance execution path and migration lifecycle.
Raw writes plus later rebuilding cannot preserve atomic uniqueness or references.
An explicit M API makes supported operations auditable and testable and requires
regeneration after schema changes. It cannot protect against raw writes or another
mutating trigger. Applications must route supported writes through the API and
coordinate other writers; this is not an authorization boundary.

## Qualification

The generated routines passed on r2.02/r2.06 with journaling off and before-image
journaling on. Tests execute plain M CRUD against managed layout-one records,
check normal Python queries/indexes, bind legacy schema bytes without conversion,
reject invalid input/corrupt metadata/maintenance, and conservatively restrict
incoming references. Forced restarts after index changes restore old state before
retry. An injected unexpected M error rolls back partial claims and propagates to
the caller's handler with tlevel zero. Coordinated first inserts produce one unique
winner while installing schema metadata atomically. The test launcher uses explicit
M arguments through %XCMD; extra shell arguments alone populate ZCMDLINE instead.

Ruff/C901, mypy, offline tests, both Pydantic matrix versions, executable docs, and
the installed-wheel smoke test passed. The wheel test exercises the packaged M
template. No trigger latency or generated-writer throughput claim is made.

## Caller-owned transaction extension

New exports add explicit inserttx/replacetx/deletetx participants, reusing prepare
and attempt. They require an existing TP and never commit it. Every failure aborts
the entire active TP, across all levels, rather than depending on a caller to honor
a rollback-only flag. Unexpected errors roll back before propagation. Existing entry
points retain their own TP and nested refusal. Inputs, schema checks, records,
indexes, and status meanings remain compatible; only participants add transaction
for missing context. The caller owns the outer restart point and must rebuild all
database-derived inputs there. This extension initially composes supported flat
models, independently of future hierarchical support.

Scoped maintenance passed both engines, both journaling modes, and native stress in
[CI 42379543484](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42379543484).
The coordinated test restores its normal routine after the rendezvous; source
replacement removes stale compiled objects before subsequent commands.


## Exact Decimal scalar extension

Export nonnullable Decimal scalars using a declared canonical-text subset of the
existing fixed 1E-18/40-unit-digit domain. Factor and reuse Python unit conversion
for generated numeric bounds; M validates canonical representations and compares
signed digit strings without rounding. Preserve raw record scale and independently
normalize equality tokens. Unchanged normalized tokens keep their claims across
scale-only replacements.

Pydantic checks both raw and normalize()-derived precision counts. Its normalization
uses process Decimal context. M conservatively requires raw counts to satisfy the
limits, guaranteeing Python acceptance without reproducing ambient context. This
restriction belongs to the explicit M input contract and changes no Python model
semantics or installed schema. Unsupported constraints fail export. Qualify canonical
bytes, exact bounds beyond 18 significant digits, zero/scale limits, unique claims,
restarts, rollback, and transaction composition before expanding other types.

Transaction participants passed both engines, both journaling modes, and stress in
[CI 42380034303](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42380034303),
including two-model rollback, nested caller levels, forced restarts, and a concurrent
Python update observed by the restarted M business calculation.


## Managed reference extension

Compile flat nonnullable Reference fields from FieldSpec and their exact registered
live target bindings. M callers provide plain keys; record and index encodings use
the target's existing str/int codec. Source and target schema checks share the same
M implementation. Customized Reference validation/configuration/aliases fail export.
Targets need not support M writes, because existence checks read their metadata and
primary identity rather than their aggregates.

Stage source nodes before checking new targets so self-reference and earlier
participants are visible. Validate old reverse entries and canonical counts, group
count deltas, then update reverse edges/counts in the same TP. Exact digit-string
increment/decrement avoids native numeric rounding. Failure aborts every staged
change through the existing owning/participating entry points. Scoped maintenance
blocks target-dependent writes but permits link removal. Target schema changes
require regenerated source routines without changing source reference identities.

M deletes remain restrict-only regardless of declared Python policies. Positive
incoming counts block deletion; corrupt metadata fails closed. A valid zero-count
namespace can be removed on deletion, matching the established Python lifecycle.
No persisted layout or metadata conversion is introduced.
