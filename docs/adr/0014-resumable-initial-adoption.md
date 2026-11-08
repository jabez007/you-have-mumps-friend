# ADR 0014: Resumable adoption with an explicitly absent source schema

Status: accepted and qualified by
[native CI 42342515333](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42342515333).

## Existing mechanisms

Small adoption already audits exact layout-one bytes and installs managed metadata,
indexes, and references atomically. Maintenance jobs already provide a gate, immutable
snapshot/operation identities, bounded phases, uniqueness reservations, recovery, and
terminal pruning. GlobalView reads arbitrary foreign layouts without rewriting them.

## Decision

Keep adopt() unchanged and add begin_adoption/advance_adoption using the same journal.
Validate all records and pin field digests before backfill. Reserve unique claims
across validation batches; recheck data and target liveness while installing normal
metadata/index/reference structures. Verify counts and expected structures before
atomic activation. Each call handles at most 100 bounded aggregates or one transition.
No field encoding or application schema conversion is implicit.

An initial job has no installed source. Record source_fingerprint=None only in
checkpoint format two, with a hashed adoption operation and target snapshot. Existing
source-bound jobs keep exact format-one bytes. Older clients reject the new format.
During adoption publish only a maintenance marker, with no installed snapshot; schema
inspection returns absence plus the job id. The shared gate protects the model while
unrelated model operations remain available to current clients.

Abort restores completely absent installed metadata before any backfill commits.
After committed backfill, require forward recovery. Raw writers must be stopped
throughout the job. Pins and counts detect changes across supported passes; they
cannot guarantee consistency against an uncoordinated raw writer after verification.
Self-references use direct target identity checks while all source records receive
full audit passes. External managed targets use existing metadata checks; ordinary
reverse-edge/count updates protect them after backfill.

## Alternatives and compatibility

A fabricated source snapshot would present unmanaged data as installed before it
was validated. A separate adoption job store duplicates ownership/recovery semantics.
One large TP preserves small-adoption simplicity but cannot bound large datasets.
The explicit absent-source checkpoint reuses existing infrastructure and fails safely
on older readers. It widens MigrationStatus.source_fingerprint's type; existing job
values and serialized bytes remain unchanged. Installed v0.3/v0.4 data is not rewritten
or invalidated merely by opening a database.

## Qualification

Both supported engines and both journaling modes passed. Tests resume 203 owned
records with cyclic self-references across reopened handles, verify unchanged field
bytes and normal index/reference behavior, and test cross-batch unique conflicts.
Fault injection covers journal creation, reservations, partial index/reference and
metadata writes, verification checkpoints, and activation. Native restart and
coordinated competing adoption/first-insert processes pass. Early abort restores
absent metadata; committed backfill resumes; terminal pruning remains bounded.
The plain-M foreign projection also passed without rewriting its source nodes.

Ruff/C901, mypy, both Pydantic versions, 1091 offline tests, executable documentation,
packaging, and an installed-wheel adoption workflow passed before native execution.
