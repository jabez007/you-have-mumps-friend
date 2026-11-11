# Integrity hardening and hierarchical mutations

This is a historical log of the 0.3.1 to 0.5 work. It does not describe current
plans. See the [changelog](../CHANGELOG.md) for what shipped and the
[issue tracker](https://github.com/jabez007/you-have-mumps-friend/issues) for what
comes next.

The requested milestones replace the previous v0.4 Go interoperability scope.
Go bindings and a shared-language runtime are deferred.

## Dependency order and acceptance

1. **v0.3.1 schema installation and verification.** Reuse `schemas`, `Store.atomic`,
   and bounded `children` reads. Reject unmanaged globals before installing
   metadata and verify installed snapshot identity during CRUD. Preserve layout
   version 1. Acceptance: regression failures before the fix, rollback tests,
   and native concurrent first writes.
2. **Explicit adoption.** Reuse record decoding, model validation, indexes, and
   references. Accept only bounded data already in the documented native layout;
   reject unknown nodes rather than dropping them. Acceptance: successful explicit
   adoption and failure leaving both data and metadata unchanged on both adapters.
3. **Fingerprint compatibility and detached writes.** Compare fixtures across
   compatible Pydantic versions before changing identity generation. Preserve
   legacy metadata and document an explicit upgrade strategy if needed. Demonstrate
   last-writer-wins detached saves and restart-safe atomic read/modify/write.
   Revision metadata is deferred unless an integrity defect requires it.
4. **v0.3.1 qualification.** Run Ruff (including complexity maximum 10), format,
   strict mypy, all available tests, native CI for both configured YottaDB versions,
   packaging, and installed-wheel smoke. Commit the milestone independently.
5. **v0.4 mutation design.** Inspect owned paths, record validation, index identities,
   reverse references, and migration barriers. Choose the smallest composable
   interface that validates the affected owner and any ancestor invariants without
   loading unrelated trees. Document rejected operations and addressing semantics.
6. **Mutation increments.** Scalar patch first, then keyed child insertion/removal
   and atomic composition. Reuse existing index and reference replacement with
   differences restricted to the changed owner. Keep whole-aggregate `save`.
   Acceptance includes constraints, rollback, unique/reference integrity, bounded
   node work, native restart and deletion/update races.
7. **v0.4 qualification.** Native benchmarks compare direct node updates, partial
   mutations, and whole replacements as unrelated child counts grow. Report
   measured latency and node operations, limitations, and all quality gates.

## Key risks

Pydantic validators may depend on whole ancestors; partial operations must reject
unsupported dependency shapes or explicitly load the state needed for validation.
Collection counts, root record budgets, nested index locations, and parent deletion
must remain transactional. Snapshot representation changes must not silently
reinterpret installed v0.3 metadata. In-memory tests cannot qualify native races
or native performance.

## Delivered boundaries

The v0.3.1 milestone is independently reviewable at `08d2086`; its full CI run
passed before v0.4 implementation started. A final maintenance review also applied
the shared snapshot check to external reference targets in `3231e6e`.

The v0.4 interface and validation policy are recorded in ADR 0004. Both native
engines passed the mutation concurrency/restart suites and ran the benchmark.
The performance report links the measured commit and CI evidence. Legacy schema
identity generation remains unchanged; the proposed successor is deliberately not
activated. Revision checking is deferred. No Go bindings, change-tracking layer,
query expansion, or native extension was added.


Final native qualification exposed a preexisting mixed-read error in competing
rebuild workers. Adapter-level bounded revalidation in `0011974` protects CRUD
and maintenance together; ADR 0005 records the reasoning and the deterministic
regression. Persistent corruption remains an error and no quality threshold was
changed.


Persistence-input regressions also confirmed that disabling Pydantic instance
revalidation could admit unchecked nested copies. `cbe6479` makes root save and
child insertion share recursive plain input construction. All four public-path
regressions fail with the old methods and pass with the fix.


## Follow-up qualification

CI action runtimes were updated to the verified checkout v7.0.1 and setup-uv
v10.3.3 releases while keeping uv 0.9.5. The native benchmark now measures equality,
unique, and reference updates, coordinated four-process bursts, callback retry
work, and separate cProfile summaries. Workload tests verify final data and
secondary structures. Native profile evidence led to a small guard dispatch
simplification without caching schema metadata or changing storage layout.

The performance report preserves both measured source commits and all results.
The optimized implementation passed 852 tests in each native environment, 443
local tests, and the dependency compatibility checks. New-schema identities,
optimistic revisions, path-construction optimization, and longer-running workload
qualification remain separate follow-ups.

## v0.5 implementation order

1. Define format-two declarations from registry field metadata and codecs.
   Reuse the existing declaration grammar and migration comparison module.
   Canonical output omits Pydantic titles, definition names, and generated patterns.
   Test golden bytes under Pydantic 2.12.0 and 2.14.3 before enabling the format.
2. Bind validated legacy snapshots to registered models without replacing their
   stored identity. Opening and writing a compatible legacy database preserves its
   metadata and record fingerprints. New installations use format two. Reuse the
   maintenance gate and saved manifests for legacy migration and rebuild recovery.
   Verify corruption rejection, rollback, and concurrent initialization natively.
3. Qualify Phase 1 independently, including packaging and both native CI engines.
4. Add bounded Hypothesis codec, schema, and operation-sequence tests with a plain
   Python reference state. Independently audit physical indexes and references.
   Put longer generated sequences and native stress in separate CI profiles.
   Add deterministic regressions for discovered defects and migration fault cases.
5. Qualify Phase 2 independently before starting the example application.
6. Build a warehouse inventory ledger with products, owned warehouse locations,
   bins and balances, independent lots, and immutable movement records. Implement
   receive, move, adjust, and bounded queries through the public interface.
   Record concrete usability findings before adding targeted diagnostics or helpers.
7. Measure inventory workflows using partial mutations and whole replacements.
   Keep direct native comparisons to operations with equivalent integrity work.
   Run full qualification and prepare v0.5.0 with compatibility and release notes.

The format version, application schema revision, layout version, and codec version
remain distinct. Custom validators and default factories require explicit
application schema revisions when their behavior changes. Legacy metadata cannot
reveal rules that its original representation omitted. Unknown formats and
unrecognized constraints must fail safely. Existing date/time and compound-key
limitations remain in place unless the example demonstrates a correctness need.

No new dependency is planned. Hypothesis is already a development dependency.
All source changes retain the configured complexity maximum of ten. The initial
complexity check passed without over-threshold cleanup candidates.

### Phase 1 qualification

Canonical generation and legacy integration are independently committed in `28c5141` and
`e24b26d`.
[CI run 42288583847](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42288583847)
passed all five jobs: 883 tests per native r2.02/r2.06 environment, 464 local tests, and
138 selected compatibility tests per Pydantic version. Both complete local non-native
suites also passed under 2.12.0 and 2.14.3. Ruff, formatting, complexity, strict mypy,
build, isolated wheel smoke, and the existing native benchmark ran. The foreign-runtime
compatibility regression fails against `268ab73` and passes with the new binding. No
dependency, record-layout, or application-data conversion was introduced. Phase 1 is
complete before Phase 2 work begins.

### Phase 2 design note

Reuse Hypothesis, the public Database interface, disposable adapter fixtures,
native worker rendezvous, and existing migration checkpoint instrumentation.
The test module will be a client of the persistence module, with a plain Python
reference state and separately derived physical index/reference expectations.
Do not call mapper codecs or entry builders to construct those expectations.
Generate inserts, scalar/reference/index patches, owned-child insert/delete,
aggregate replacements/deletes, invalid writes, uniqueness conflicts, restricted
deletes, and explicit atomic aborts. Verify logical and physical state after each
step, including absence of stale entries. Existing native races already cover
children, fields, uniqueness, parent deletion, references, and forced TP restarts;
extend them for a warmed legacy schema changing during an active transaction.

Keep routine examples bounded. A separate stress profile and CI job will run
longer generated sequences with Hypothesis reproduction output enabled. Native
state-machine examples remain bounded and use the same independent assertions.
Add migration interruption cases at installation and committed-batch boundaries
alongside existing field/index/checkpoint/activation rollback cases. No production
abstraction or public API change is planned unless a generated case proves a defect.
The source complexity check remains at ten and passes before this phase.

Phase 2 boundary testing confirmed that unpaired Unicode surrogates escaped from
both scalar and key encoding as `UnicodeEncodeError`. Both minimal regressions
failed before the fix. The shared UTF-8 encoder now raises a value-free
`StorageError`; valid encodings and layout bytes are unchanged. Generated nested
round trips also check Decimal scale, null/empty/missing distinctions, binary data,
integer child identities, schema ordering, malformed bytes, and size/depth limits.

### Phase 2 qualification

The codec fix (`c93778e`) and adversarial tests (`0b57ac0`) are independently committed.
[CI run 42290473930](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42290473930)
passed all six jobs: 916 tests in each native environment, 14 additional native stress
cases per environment, 483 local tests, 146 selected tests per Pydantic version, and 19
cases in the separate memory adversarial stress job. The longer local state-machine run
passed in 18.24 seconds; the complete routine local suite passed in 5.98 seconds.
Ruff/complexity, format, strict mypy, packaging, isolated wheel smoke, and native
benchmarks passed. No generated mutation integrity defect was found; the confirmed codec
error boundary received minimal failing regressions. Phase 2 is complete before
application implementation begins.

### Phase 3 design note

Use independent Product and Lot roots so inventory and historical movements can
reference stable identities. A Warehouse owns Locations, which own Bins, which own
Balances keyed by lot ID. A Balance also stores indexed product/lot references:
the product reference is intentional denormalization for stock-by-product lookup.
The application transaction verifies it against the Lot before any balance change.
Movement roots preserve independent audit identity and indexed lot/product history.
No root/ancestor validator will obstruct bounded owned mutations; the service
transaction owns cross-record and cross-balance business invariants.

Expose receive, move, and justified adjustment through a small example service
using only public persistence APIs. Caller-supplied movement IDs stay stable across
transaction restarts; duplicate IDs abort the complete transaction. Quantities
must be finite and nonnegative in balances; movements may contain signed adjustment
quantities. Use explicit location paths and plain UTC timestamp strings because
date/time codecs remain unsupported. Include a memory demo and an opt-in native
run against a configured database, with physical paths documented.

First test the workflows on both adapters, including insufficient stock, failed
ledger insertion, reference restrictions, rollback, and a native forced restart.
Then record concrete ergonomics findings. Prefer a targeted missing-index diagnostic
over new query/path frameworks. Benchmark equivalent adjustment/receive/move
transactions using partial mutations versus grouped whole-aggregate replacement;
report direct native leaf assignment only as a lower-bound workload without ledger
or integrity work. Reuse existing measurement/counter helpers where appropriate.
The source complexity baseline remains at ten; no new dependency is needed.


Phase 3 implementation slices are committed in `02f0c40` (application), `b2d7d16`
(diagnostics), `fbdbc61` (shared benchmark reporting), and `86afd26` (equivalent
native workflows). The final contract review added equivalent-Field/default-policy
regressions in `74283d1` and retained both canonical and legacy comparison
expectations in `3bba6bf`. Both new regressions failed before the producer compiled
mapper flags from actual registry semantics. Legacy fixtures remain unchanged.
The historical presentation comparison expectation was updated after its full-suite
failure; both complete local Pydantic suites subsequently passed 514 tests.


### Phase 3 completion

[Final contract run 42294677787](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42294677787)
passed all six jobs: 970 tests per native environment, 14 additional native stress cases
per engine, 169 compatibility cases per Pydantic version, and the 19-case adversarial
job. Both complete local Pydantic suites passed 514 tests. Ruff, formatting, complexity
maximum ten, strict mypy for the library and example, packaging, and isolated v0.5.0
wheel smoke passed. Native tests verified double-spend prevention, forced workflow
restart, and ledger/index rollback; the native demo and both benchmark runners completed
on r2.02 and r2.06.

The inventory benchmark's raw samples, node-operation counts, CPU/runtime metadata,
and source commit are committed with the performance report. The runnable example,
concrete ergonomics findings, compatibility policy, and evidence-based v0.6 priorities
are documented in the v0.5.0 delivery report. All three implementation phases are
complete. Version 0.5.0 preserves layout one, legacy identities, namespace, and GPL
licensing, and adds no dependency. Release tags and package publication are separate
repository operations.

## Review issue delivery

The issue review at `63ad653` identified 35 follow-ups. The approved pushback is
posted on issues 1, 2, 3, 5, 11, 15, 17, 18, 19, 23, 24, 25, 26, 27, 28, 29, 30,
32, 33, 34, and 35. Implement the valid work within those recommendations, with
independently verified commits pushed after each slice. Keep design proposals
open until their acceptance criteria are met; comments alone do not complete them.

Delivery order:

1. Transaction control and lifecycle: swallowed native signals and shared-handle
   nesting, issues 1 and 7. Preserve the existing stable validation retry policy.
2. Declaration and filter correctness: reject lossy extra-field policies on root
   and owned models, issue 3; share configured filter validation, issue 5.
3. Schema revision and validation-only migration execution, issue 2, including
   explicit unsupported reasons and safe recovery boundaries.
4. Remaining error and registration contracts: null collections, large integers,
   referrer diagnostics, and region-size errors, issues 8, 10, 9, and 6.
5. Fixed-width child insertion counting and native measurements, issue 4, followed
   by declaration-scoped guard/adapter optimization, issue 31.
6. Bounded collection, occurrence, batch, reference, and inverse reads, issues 30,
   16, 12, and 14. Keep raw-write design separate from collection paging.
7. Migration housekeeping, transforms, and scoped maintenance, issues 22, 20, and
   21, as separate designs and increments using existing maintenance infrastructure.
8. Complete issue 34's regression, journal, documentation, and packaging checks
   alongside the work they verify. Publish factual licensing documentation for 35.
9. Address the remaining proposals through the approved smaller designs and
   feasibility checks, including types, grouped mutations, typing, M interoperability,
   computed fields, extension, access rules, encoding, contention, and error retries.
   Preserve detached-save semantics when evaluating issue 27's write optimization.

### Transaction control design, issues 1 and 7

Reuse NativeStore's get/set/delete/traversal seam, its TP callback wrapper,
MemoryStore's reentrant lock and rollback snapshot, and Database's expiring handles.
The missing invariant is that a native restart or rollback invalidates the whole
attempt even if caller code catches its exception. Record the first control signal
at the native-call seam and propagate it when the callback ends. Later native
calls in that attempt must also refuse to continue. A fresh attempt gets fresh
state. Preserve native signal identity and the existing application-error retries.

NativeStore instances share the process's nonthreaded YottaDB engine, so an active
callback context must also reject nested atomic entry through another handle or
store instance. MemoryStore owns an independent database per instance; reject its
reentrant atomic entry while holding its existing lock, without rejecting serialized
work from other threads or transactions on independent memory stores. No public
method or storage layout changes. A flag only on Database is insufficient, and
changing upstream exception inheritance globally would break its public contract.

Deterministic regressions cover all native-call methods, caught restarts/rollbacks,
post-signal calls, fresh retries, and shared-handle nesting. Native tests add a
forced swallowed restart and four-process counter increments. Existing failure
revalidation and rollback tests must remain intact. The initial C901 check passes
at the configured maximum of ten. Native CI on both supported engines remains
required before closing these issues.

### Declaration policy design, issue 3

The registry already visits every root and owned NodeSpec. Reject extra='allow'
at that seam because record input extraction intentionally persists only declared
fields; accepted extra values would otherwise disappear. Do not add a second
field-enumeration persistence path or change canonical fingerprints to describe
unsupported storage. Reject nullable owned declarations too, even when their
current value is null. Extra='ignore' remains ordinary Pydantic input behavior.

The existing input_values helper recursively converts owned instances into plain
mappings before validation. This already protects writes with instance revalidation,
default validation, or assignment validation disabled. Scalar codecs independently
reject nonfinite values. Prove those safeguards with a configuration matrix and
keep their existing canonical fingerprints unchanged. This requires no format-two
metadata conversion. Reuse existing validation errors and value-free SchemaError
messages. The configured complexity check still passes at ten.

Transaction slice `22001f6` passed all six jobs in
[CI run 42309069293](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42309069293).
Both native engines executed the controlled caught-signal tests and four-process counter
regressions. Issues 1 and 7 are closed after that qualification. The configuration
matrix for issue 3 has five failing-before-fix declaration regressions; all safe-option,
rollback, and fingerprint checks pass under both Pydantic versions.

### Field validation design, issue 5

Mutation validation already combines a field's rebuilt annotation with its owner's
Pydantic config, while indexed and primary-key query validation do not. Extract
that existing adapter construction into the registry module, which owns field
declarations, as a behavior-preserving refactor. Reference models keep their own
config. Then make queries reuse the helper without forcing strict=True over the
declaration. Preserve codecs, stored indexes, signatures, and schema identities.
Single-field queries do not execute arbitrary cross-field model validators. Add
regressions for string transforms, bounds, nonstrict fields, nested owners, primary
keys, and nullable references before changing the query callers.

### Validation-only migration design, issue 2

The planner already compares root and owned revisions, constraints, nullability,
and defaults. The executor already validates both declarations and refuses changes
to existing field bytes. Extend those mechanisms rather than adding a migration
API. Validation-only plans use validation, verification, retagging, and activation;
they skip the backfill pass. The historical backfilled counter records readiness
for verification, including records requiring no writes. Existing checkpoints and
additive migrations retain their phases and recovery behavior.

Expose value-free execution blockers in plan documents so structural limitations
are distinct from manual review. A record validation or normalization failure in
the initial validation-only pass rolls back before restoring source access in a
separate transaction pinned to the rejected checkpoint. Native stable-error retries
revalidate failures before exposing them. Storage faults remain resumable failures,
and failures after retagging cannot automatically restore the source. If another
worker changes the checkpoint before restoration, retain maintenance for inspection
and resume. Reuse the maintenance abort helper; never nest TP.
Preserve application values, indexes, metadata formats, and legacy bindings.

Regression tests cover the reported five changes, stricter constraints, owned
revisions, restart/reopen, normalization rejection, and failures before activation.
Existing additive failure and recovery tests stay intact. C901 passes at ten
before implementation.

### Null ownership design, issue 8

Reuse collection_count and require_object's absence policy: a null marker with no
descendants means NotFound; null with descendants and malformed collection markers
remain StorageError. Leave null collections null rather than creating owners.
Exercise get, patch, insert_child, and delete_child with rollback assertions. No
API, schema, or layout changes; configured C901 passes at ten.

### Integer error design, issue 10

The scalar codec is the existing error boundary for unsupported representations.
Translate Python's decimal integer conversion limit into a value-free StorageError,
preserving its ValueError inheritance. Reject oversized encoded keys before integer
conversion. Keep current encodings, interpreter limits, and finite-number rules.
Prove both codec failures and rollback of prior atomic writes. C901 passes at ten.

### Reference binding diagnostics, issue 9

Keep Registry's exact-declaration binding and reference_fields traversal. Name the
source declaration, field, and missing target at validation rather than falling
through to the generic database-access error. Document explicit target rebinding
and prove that unchanged target identities need no referrer migration. Resolving
by global name would silently substitute Python validation behavior and requires
a separate contract. No schema, layout, or API changes; C901 passes at ten.

### Native region diagnostics, issue 6

Extend NativeStore's existing call boundary, after its control-signal handler.
Translate only YDB_ERR_REC2BIG and YDB_ERR_KEY2BIG by their documented numeric
codes into StorageError with the setting name and setup guidance. Chain the
original exception so callers retain code() and native diagnostics. Other engine
errors and TP signals keep their behavior. Do not change region configuration.
Add isolated default-region subprocess tests on both native CI engines, alongside
adapter regressions for code selection and unchanged unexpected errors. C901
passes at ten before the change.

### Child insertion budget design, issue 4

The insertion path reads ownership markers to enforce the 10,000-node aggregate
limit; it does not deserialize the full aggregate. Its flat child walks are still
unnecessary. Reuse each collection's transactional c:N marker and its compiled
FieldSpec to count fixed-width child subtrees by multiplication. Descend only into
variable-width owned containers. Preserve the old layout, limits, validators, and
secondary maintenance. Counts are authoritative mapper metadata, not a substitute
for auditing arbitrary raw writes; full reads still check their enumerated keys.

Compare adapter operations across flat collection sizes and preserve near-budget,
malformed-marker, nullable, nested-container, and rollback regressions. Add native
insertion benchmarks for growing target and unrelated collections, comparing direct
node insertion, insert_child, and atomic whole replacement. No claim about block
conflicts follows from operation counts. Existing C901 passes at ten.

### Declaration compilation design, issue 31

Reuse immutable Registry-owned ModelSpec, NodeSpec, and FieldSpec objects. First
move validator classification from owned traversal into the registry declaration
module as a behavior-preserving refactor. Then compile that policy and scalar
TypeAdapters at registration and reuse them in queries and partial mutations.
No process-global cache or database-metadata cache is introduced. Separate model
classes with equal fingerprints retain separate Python validation behavior.

Declarations are fixed after registration; changes require a new class and registry.
Make rebuilt Pydantic schemas fail clearly on access rather than using stale compiled
rules. Continue reading the live installed metadata pair in every transaction.
Golden fingerprints and storage bytes must remain unchanged. Test same-fingerprint
classes with different validators, Annotated/ancestor guards, reference adapters,
model configuration, and live schema corruption. Use native before/after benchmark
and profile output; do not infer latency improvements from cache-hit counts. C901
passes at ten before the changes.

### Bounded collection reads, issue 30

Reuse owned.resolve, collection_count, the existing child-collection resolver, and
the owned-record reader. Move the latter two helpers to shared seams before adding
reads. Expose children(model, key, collection=..., limit=100, after=None) and
count(model, key, collection=...) without loading the owning root. Each page is one
transaction, ordered by the child's native key; cursors are exclusive keys, not
snapshot tokens. Null collections raise NotFound and corrupt markers fail safely.
The count trusts the authoritative transactional marker rather than scanning keys.

Test 25-of-1,000 node work, typed numeric/string keys, null and missing owners,
expired handles, maintenance, and paging under key changes. Raw access is a separate
contract: the existing Store and Path escape hatch bypasses mapper invariants.
Keep issue 30 open until that controlled interface is decided and delivered. No
layout or fingerprint changes. C901 passes at ten before this work.

### Explicit reference and batch reads, issues 12 and 16

Reuse the registry's concrete Reference target extraction, Database.get, _run, and
the query page bound. Add typed resolve(Reference[T]), resolve_many(sequence), and
get_many(model, keys). Each call uses one transaction, preserves input order and
duplicates, and reads each distinct target once. Deduplication is local to a single
callback attempt, never an identity map or persistent cache. Validate the finite
sequence bound before database work and validate every key before cache lookup.
Missing targets raise value-free NotFound; no implicit attribute loading.

Lists contain detached models, and duplicate positions share the fetched instance
within that result. Empty batches are allowed but still respect active handles and
maintenance. Native restarts reconstruct the attempt's cache. No metadata or layout
change. Occurrence projection for issue 16 is a separate subsequent slice; keep that
issue open until it ships. C901 passes at ten.

### Qualification gaps, issue 34

The preceding fixes supply the configuration matrix, caught-restart, default-region,
and insertion benchmarks. Reuse test-native.sh for a second isolated database with
before-image journaling enabled by an explicit environment flag; run the full native
suite and benchmarks on both supported engines. Record the mode in benchmark output.
This qualifies journal-enabled operations, not crash recovery or backup restoration.

Run executable Python fences in isolated per-document namespaces through a small
checker, preserving their document line numbers. Mark architectural sketches
explicitly illustrative, and repair incomplete runnable examples. Test the checker
through real documents and a deliberately failing example. CI runs it with the
locked environment. Shell and M snippets retain their native integration harnesses.
Make README file links absolute GitHub URLs and verify their local targets for the
future PyPI long description. No runtime API or storage change; C901 passes at ten.

### Component license sources, issue 35

Reuse LICENSE, pyproject.toml, and the architecture's license section. Publish a
factual page linking upstream engine and driver COPYING and LICENSE notices,
including the driver's additional upstream clarification. Record Pydantic's MIT
metadata without inferring legal obligations for an application. Remove the
architecture's unreviewed deployment interpretation. Legal advice is an external
remaining requirement, so issue 35 stays open. No dependencies or runtime changes.

### Retire terminal maintenance jobs, issue 22

Reuse migration_cleanup's bounded first-leaf deletion, terminal MigrationStatus,
job_path validation, and NativeStore.atomic. Extract leaf deletion without the
cleanup-counter side effect first. Add prune_migration(id, batch_size=25), returning
whether retirement is complete. Each call removes at most 100 requested leaves;
the job root is deleted last. Absent jobs return complete. Refuse active jobs and
gate owners, while permitting retirement of an unrelated terminal job.

An optional pruning=true checkpoint flag marks intentional partial retirement.
Omit it from ordinary checkpoints to retain existing bytes. Lifecycle executors
reject marked jobs clearly instead of treating removed snapshots as corruption.
Pruning requires no model bindings and can reopen after every committed batch.
Older maintenance readers reject the new optional flag; ordinary application
records, installed schemas, indexes, references, and other jobs remain unchanged.

Source-free execution of an unfinished migration cannot reconstruct arbitrary
Python validator behavior from schema metadata. Retain exact source and target
declarations until completion or safe abort; then retire them and the job according
to the application's audit policy. Test bounds, active rejection, reservations,
resume/idempotence, rollback, and index-rebuild jobs on both adapters. C901 passes
at ten before implementation.

### Indexed occurrence projections, issue 16

Equality leaves already contain canonical concrete locations keyed by root and
location digest. Reuse those bytes, typed owned paths, get(at=...), codecs, and
index entry builders. Add query.occurrences(limit=100, after=token) returning
Occurrence(root_key, at, field, record, cursor). Each record is the complete model
owning the indexed field. Indexed leaf balances therefore avoid loading unrelated
warehouses; an index on a root field still returns its complete root owner.

Page in existing native root-key order, then digest order, without new indexes.
Opaque versioned cursors bind to the installed fingerprint and normalized predicate.
They are bounded position tokens, not encrypted identifiers or snapshot guarantees.
Deleted cursor entries remain usable; separate pages can see intervening writes.
Keep direct-key lookups on get/all rather than invent occurrence cursors for them.

A small occurrence module owns bounded entry traversal and location decoding, also
needed by reverse-reference reads. Validate canonical location bytes, digest, exact
declaration path, owner identity, selected field bytes, and the actual index entry.
Do not accept another matching child as evidence for a forged occurrence. Strengthen
root query entry checks through the same existing entry builders. Preserve root
query signatures, storage layout, and fingerprints. Tests cover bounded reads,
pagination/cursor scope, corruption, rollback, native adapters, and inventory paths.
Existing C901 passes at ten; keep parsing and traversal responsibilities separate.

### Read-only inverse relations, issue 14

Reuse reverse-reference counts and concrete leaves, the occurrence decoder, native
two-level paging, typed owner reads, and normal Link generation. Expose
referrers(reference, source=SourceModel, limit=100, after=cursor). Requiring the source
declaration selects its native prefix directly and supplies Python validation;
there is no scan across model globals. Verify the target's schema and record metadata
without loading its aggregate. Validate every exact source field and reverse edge.

Return the same Occurrence values and cursor semantics as equality projection.
Counts remain authoritative metadata, not a full reverse-index audit. No new relation
storage is needed. Demonstrate many-to-many with Supplier.products keyed owned links
referencing independent Products; inverse reads retrieve supplier links directly.
Add only the Supplier global, leaving existing inventory contracts unchanged. Cover
typed pagination, null/changed/removed links, corruption, rollback, restrict deletion,
and bounded work on memory and native adapters. C901 passes at ten.

### Scoped raw addressing, remaining issue 30

The existing public Store and Path already supply native byte operations. Add only
raw_path(model, key, at=...) on an active atomic handle, reusing owned.resolve to
check the installed schema, managed record metadata, and owned markers. This avoids
a parallel raw persistence wrapper and takes trusted model declarations rather than
caller-supplied global names. A resolved Path remains an address, not an authority
token; use it and Store operations in that callback.

Document the exact bypasses: raw bytes do not run Pydantic validation or maintain
indexes, uniqueness, references, child counts, or application rules. Uncaught errors
and native TP signals retain transaction rollback/restart behavior; caught raw errors
do not acquire mapper write-poisoning semantics. Raw addressing does not install
schemas or adopt unmanaged data. Test bounds, expiry, rollback, owned addresses,
schema refusal, and demonstrated stale indexes after intentional raw writes. No
metadata or layout change; C901 passes at ten.

### Compile-time field reuse, issue 18

Registry and Pydantic already support inherited model_fields. Capture current
inventory snapshots before factoring their repeated nonempty string id into an
Identified base. Follow ADR 0007: concrete globals stay explicit, ownership and
References keep their existing roles, and shared-global variants are deferred.
Verify byte-identical contracts, legacy metadata, indexes, and additive migrations.
This is a behavior-preserving example refactor, with no mapper API or storage
change. Existing C901 passes at ten.

### Rollback before automatic migration abort, issues 2 and 33

An adversarial validator exposed a defect in the original automatic-abort design:
returning an error sentinel committed writes made by the rejecting validator.
Adapter-level instrumentation cannot prove that arbitrary validation is read-only.
Raise the rejection through Store.atomic, preserving native stable-error retries
and rolling back every database write. Only then restore the source with the
existing abort mechanism, comparing the current checkpoint to the rejected one
inside its transaction. A concurrent change fails conservatively under maintenance.
There is a brief maintenance interval between rollback and restoration; interruption
there is recoverable by retry or explicit abort. No metadata format changes.
The regression fails before the fix (stored amount 4 instead of 2). Add native raw
write coverage and checkpoint-race coverage. Existing C901 passes at ten.

### Stable-error retry decision, issue 33

Keep NativeStore.atomic and ADR 0005's stable-error policy. Raw calls and arbitrary
validators make adapter write tracking insufficient evidence for an error-only
successful commit. Add a native counterexample contrasting an unsafe sentinel with
actual rollback, plus an ordinary unittest.mock boundary guard demonstrating what
can be tested without promising universal side-effect detection. Document the
decision and the absence of a performance claim. No new dependency, storage change,
or retry framework; existing C901 passes at ten.

### Authorization decision, issue 19

Database has no principal model and already exposes raw and maintenance interfaces.
Reuse the application's service boundary instead of adding a partial query filter
that implies protection on other access paths. ADR 0008 records the decision and
the independent M-writer limitation, supported by upstream security documentation.
Document that inventory's business validation is not authorization. No runtime,
storage, or dependency change; validate documentation examples and links.

### Virtual computed values, first slice of issue 17

Pydantic computed_field already computes Python values, and records.input_values
stores only compiled model_fields. Registry currently rejects computed declarations
entirely. Permit virtual computed fields, but reject mapper persistence metadata on
them. They create no nodes, indexes, schema identity, or dependency machinery. Reads
compute from the loaded value object; cached properties retain Python's ordinary
staleness semantics. Demonstrate an uncached Warehouse.total_on_hand with exact local
Decimal arithmetic. Verify canonical/legacy compatibility, nested patches, save,
index/query refusal, and no persistence-time evaluation. Stored rollups stay open
pending the independent-writer contract in issue 25. Existing C901 passes at ten.

### Explicit grouped patches, issue 23

indexes.replace already releases all removed claims before claiming new values.
Extract mutation preparation from its writes in a separate behavior-preserving
commit, retaining field/model validation and changed-node selection. Then expose
Patch and patch_many for 1–100 explicit scalar/reference owner changes. Prepare
the whole group against current transaction state, reject duplicate owner addresses,
combine normal secondary entries, and apply one index/reference transition before
writing changed nodes. This permits swaps without deferring constraints across an
arbitrary callback. Reads before/after the call see complete valid states; no user
callback runs between group writes. All errors poison the active write transaction.
Group input is copied before retries. Reuse existing single-patch preparation and
index/reference builders, not a deferred-constraint subsystem. Ancestor validation,
bounded ownership reads, layout, and schema identities stay unchanged. Test swaps,
duplicates, invalid groups, rollback, references, bounded work, restarts, and an
independent native claimant. Existing C901 passes at ten.

### Typed selectors and reference factories, issues 28 and 29

A strict-mypy spike in /tmp verifies that Callable[[Warehouse], Balance] selection
infers ModelPath[Balance], detects a misspelled locations field, and that a typed
factory inferred from Product.id rejects an integer key. Use those standard typing
features rather than dynamic class attributes, generated stubs, or a mypy plugin.
Implement bounded declaration probes for field access and keyed ownership only.
Database.path returns a frozen declaration-bound ModelPath; get checks its binding
and returns the selected model type. Tuple paths remain supported. Registry.reference
selects the declared primary key and returns a typed callable constructing the
existing Reference[Target], preserving legacy generic identity and schema bytes.
Selectors run application Python and must be pure; they do not load records or form
an authorization boundary. Runtime checks still reject wrong keys and forged paths.
Replace actual inventory get casts, not domain business rules. Query-field objects
remain separate from owned-path selection; no new query language is introduced.
Test positive and negative strict-mypy fixtures, schema compatibility, nulls, wrong
root/target, expiry, bounded reads, and native adapters. Existing C901 passes at ten.

### Aggregate replacement node differences, issue 27

save already validates a full input and loads the previous aggregate for secondary
maintenance. Preserve that whole-value, last-writer-wins contract; a node difference
cannot infer which edits the caller intended. Use the existing Store interface to
take a bounded physical subtree snapshot. First extract adoption._nodes into
storage.subtree_nodes, preserving the adoption budget and conservative full-page
rejection. Then compare record_nodes, delete only minimal
removed prefixes, and write changed values. Audit actual nodes so replacement still
removes undeclared raw data. If an extra value occupies a retained parent, deleting
it also deletes descendants; explicitly rewrite all desired descendants of that
deleted prefix. Bound visited addresses as well as values, with O(nodes * depth)
prefix checks rather than pairwise subtree comparisons. Keep existing index/reference
replacement. Inserts remain direct writes. Physical auditing adds traversal/reads;
measure native replacement benchmarks and claim reduced writes only unless latency
measurements support more. Oversized raw subtrees now fail safely for explicit repair.
Test no-op/one-field writes, owned removal/nulls, raw parent values, rollback, legacy
metadata, and generated equivalence against delete-and-insert. Existing C901 passes
at ten; no optimistic revisions or automatic change tracking.

### Model-scoped maintenance access, issue 21

Keep the existing single job coordinator and per-global fingerprint barrier. Its
checkpoint already identifies the migrating global. Let ordinary schema checks
load and validate that checkpoint/ownership inside TP and block only that global;
do not add a dependency graph or a second lock system. All reference targets already
go through schema checks, and deletion already reads maintained inbound counts.
Thus a new/validated reference to the migrating target blocks; unrelated scalar
patches and detached source reads can proceed. Full saves revalidate every nonnull
target and may block. Removing an incoming edge or source is safe and uses existing
atomic counts. Deleting a target referenced by the migrating model remains restrict.
Any cross-model callback needing the gated model rolls back; a second maintenance
job remains refused. Empty resolve batches access no model. Explicit adoption stays
serialized with other maintenance. Older clients still block globally and respect
the same schema barrier. Unknown/corrupt active jobs fail closed for ordinary access.
Test all edges, interrupted/resumed jobs, index rebuilds, and two-process unrelated
CRUD with barriers on native. No metadata/layout change; existing C901 passes at ten.

### Explicit record transforms, issue 20

Reuse MigrationPlan, job snapshots/operation fingerprints, index-rebuild clearing,
uniqueness reservations, counters, abort/pruning, and atomic activation. First share
operation-document integrity checks and target-metadata validation in a refactor.
Add an explicit named Transform(source -> target) to plan/begin/advance, with the
same root global and key identity. Unknown formats, layout changes, and root-key
changes remain blocked. The application owns the stable transform revision; never
hash function bodies or silently select code from stored identifiers.

Validate every source, target, old secondary entry, and proposed target reference.
Reserve final unique claims across batches. Save per-record source/target node
digests under hashed key addresses, including the full key to reject collisions.
Backfill must reproduce both digests, detecting changed data and nondeterministic
defaults before rewriting a record. Clear this model's indexes in bounded batches
before transformation, allowing unique-value exchanges without temporary values.
Then rewrite each record's target fields while maintaining reverse links/counts in
that transaction. Keep source record metadata until verification and the ordinary
retag pass. Activation publishes the target only after all counts agree.

Abort is allowed before the first committed index deletion or record rewrite;
afterward recovery requires resume or backup restore. The review document states
that boundary. Transform exceptions roll back their batch and retain its checkpoint,
never commit an error sentinel. Jobs carry the existing custom-operation marker,
so older executors refuse them; ordinary databases are not converted on open.
Source reference path bindings may differ from target bindings and must be compiled
separately. A full rewrite removes obsolete fields/owned nodes; no staged copy of
an aggregate is stored. Test each requested transformation with native interruption
and resume, uniqueness, references, restarts, legacy metadata, determinism, corrupt
digests, failed callbacks, and pruning. Existing C901 passes at ten.

Transform preparation also exposed that Link equality included target Python class
identity, despite unchanged native target, location, and edge bytes. The regression
fails for equivalent target rebindings. Exclude that validation-only class from Link
equality/hash before using distinct source/target bindings; keep all native identity
fields and target validation intact. No storage or schema change.

### Guard registered storage identities, follow-up to issue 31

Registry captures core-schema identity and config but not __global__ or
__schema_version__. Mutating a registered revision before first insert can write
record metadata inconsistent with the installed snapshot; bool/float equality with
integer revisions can also hide a change. Extend the existing declaration state to
capture both class variables for root and owned models, enforcing exact int revision
types. Use a small immutable state value instead of expanding positional tuples.
Reject mutation before access and require fresh declarations/registries, preserving
all valid schema bytes. Test empty and installed databases with root/owned changes
and verify no writes on failure. Existing C901 passes at ten.

### Date and datetime codecs, first slice of issue 11

Use the existing scalar codecs, field adapters, canonical declarations, indexes,
and literal-default migration path. Add D:YYYY-MM-DD and fixed-width
T:YYYY-MM-DDTHH:MM:SS.ffffffZ encodings. Datetimes represent UTC instants; require
aware inputs, reject UTC overflow, and do not preserve zone/fold presentation.
Reencoding rejects noncanonical raw values. Keep layout/global codec version one
and every existing scalar encoding/fingerprint unchanged. New fields explicitly
declare date-v1 or datetime-utc-v1 codecs. Canonical temporal bounds use their own
named rules, avoiding numeric rounding/normalization; comparisons recognize those
formats and reject unknown temporal codecs. Defaults use canonical ISO strings,
which existing strict JSON literal validation can reconstruct.

Verify golden bytes/contracts, equality/unique indexes, boundaries, nullable and
nested fields, migrations/defaults, rejected naive/invalid values, and native
adapters before changing the sample ledger. Then add an explicit named migration
from legacy string Movement timestamps to an application-revision-two datetime
model, preserving string timestamp inputs at the service boundary. Do not silently
invalidate existing example databases. UUID/enums/literals and hierarchical scalar
lists remain separate slices. Existing C901 passes at ten; extract temporal codec
logic rather than raising complexity thresholds.

Temporal codecs passed native CI 42328226444 before changing the ledger. Preserve
explicit legacy Movement/Position/quantity declarations for migration rather than
sharing a mutable historical base with future domain changes. This intentional
small duplicate freezes the old contract; the existing inventory fixture verifies
its exact bytes. The service keeps ISO string inputs and also accepts datetime,
fixing a UTC instant before atomic work. Returned Movement.occurred_at becomes
datetime and the example schema revision increases to two. Migration remains an
explicit plan/begin/advance operation, including interrupted and invalid legacy
timestamp tests. Other inventory model identities must stay unchanged.

### UUID and finite choices, next scalar slice of issue 11

First replace the growing scalar encoder branch chain with exact-type dispatch,
preserving Decimal subclasses, finite checks, key behavior, and all golden bytes.
Then add UUID's canonical lowercase tag and finite Enum/Literal declarations using
the existing primitive storage tags. Compile enum lookup maps once for exact typed
restoration; never invoke _missing_ for unknown stored values. Canonical contracts
record sorted primitive values, not enum names/classes/order. Homogeneous str/int/
bool choices only; reject Flag, mixed choices, and enum use_enum_values mode until
its strict revalidation semantics are supported deliberately. Existing adapters
validate input and queries, while field codecs restore the declared Enum on reads.
Type changes to Enum require an explicit transform. Existing primitive, temporal,
and legacy contracts must remain unchanged. Test null/defaults, native indexes,
unknown choices, aliases, rollback, canonical/codec goldens, and both Pydantic
versions. Scalar lists retain their separate hierarchical design. C901 passes at ten.

### Hierarchical scalar lists, final type slice of issue 11

Reuse FieldSpec, field adapters, record encoding/reading, equality indexes, prepared
patches, and the existing root node-budget traversal. Store a list as l:N with
canonical numeric 1..N item nodes, at most 1000 items. Preserve empty, null, and
missing distinctions; items are supported plain scalars or finite choices, with
nullable items allowed. Reject nested containers/models/references and Annotated
item declarations initially; whole-field validators and length constraints remain
supported. Whole-list equality preserves order and duplicates, subject to native
index-key limits. No membership query or automatic item tracking.

Extract the existing node-budget visitor from child mutations first, so list growth
and child insertion use one invariant. Factor leaf reading before extending it.
Then compile an item FieldSpec and explicit scalar-list-v1 schema grammar. Compare
encoded field node maps to prepare minimal sets/deletes; grouped patches check net
root growth once before writes. Fixed-width child counting must exclude list-bearing
children. List type/domain changes and new list fields use explicit named record
transforms initially, avoiding ambiguous binary/Enum literal reconstruction in the
ordinary scalar-default executor. Existing primitive/default migration behavior and
all stored contracts remain unchanged. Test physical shape, corruption, limits,
index claims, shrink/null transitions, grouped swaps, rollback/restarts, owned lists,
transform recovery, and both Pydantic/native matrices. Existing C901 passes at ten.

List migration checks reproduced an older inspector defect: a revision-only
migration of Decimal | None was reported unsupported because its canonical nullable
wrapper contains Decimal's number/string alternatives. Flatten resolved anyOf
branches with bounds of 31 levels and 128 alternatives, carrying inherited rules
into every leaf. Keep ambiguous unions rejected by the existing type signatures.
No snapshot or record bytes change. Deliver this regression fix separately before
list integration; its memory/native test verifies validation-only activation and
unchanged Decimal bytes/indexes.

### Bounded reference deletion policies, issue 13

Reuse reverse-reference counts and edges, occurrence address validation, owner
resolution, prepared patches, normal secondary transitions, and collection length
checks. Keep restrict as the default and preserve its existing schema bytes. Record
set_null/cascade in the reference contract; require nullable set_null declarations.
A policy-only change is a reviewed validation migration, not a data rewrite.

Discover the entire cascade closure before writing or deciding restrict conflicts.
Cascade deletes the nearest keyed owner of the incoming reference; a reference in
an embedded value belongs to its nearest keyed child or independent root. Parent
removal dominates overlapping owned deletions. Cycles terminate through native
owner identities; restrict edges internal to the closure are released together.
Defer set_null changes until the closure is known and combine changes per surviving
owner. Preserve full leaf validation and existing ancestor-validator restrictions.

Bound each delete attempt to 100 distinct decoded owners, 1000 incoming edges,
and 10,000 distinct decoded value nodes. Use bounded physical reverse-subtree reads
and exact count/edge validation, rejecting malformed or unregistered sources. These
limits raise MutationError before writes; there is no partial progress. Incoming
sources must have exact registered declarations, since stored snapshots cannot run
application validators. Decode/cache owners only within this transaction attempt.
Raw missing edges remain outside the supported-writer contract; do not claim a
source-global scan audits them.

Prepare final collection counts, root growth after deletions, and union index/link
transitions before applying anything. Release all removed links/claims, install
surviving patch changes, and delete physical units/counts in one native transaction.
Check new targets and reject surviving links to deleted roots. Keep the final
restrict count check as a safety invariant. Whole-root deletion remains available
with custom root validators; partial child/null updates retain existing policy.

Before implementation, move existing collection-length and grouped-growth checks
to the ownership/budget modules and separate occurrence field-byte validation from
owner fetching. This refactor preserves behavior and allows the planner to reuse
validated attempt-local owners without parallel integrity implementations. Existing
C901 passes at ten. Test nested policies, mixed-policy cycles, closure overlap,
bounds, null validation, claims/reverse counts, rollback/poisoning, migrations,
unregistered sources, malformed reverse data, native races, and native restarts.

### Measure record-layout tradeoffs before ordered queries, issue 24

Reuse version-one scalar/key goldens, the existing native M subprocess tests, and
qualified CI engines. Compare tagged reads and untagged numeric values with real M
arithmetic, high-precision payloads, bool/int and null/empty distinctions, and native
subscript collation. Add a small explicit-tag M payload reader, qualified against
committed goldens; it is not a validator or writer. Preserve existing data/layouts
while collecting evidence. Record raw outputs per engine/journaling mode and then
choose whether a record-layout conversion is justified. Ordered index encoding is
an independent persistence contract; investigate it separately rather than making
range queries depend on replacing record scalars. No performance claim follows from
these semantic probes. Python C901 remains at ten; no application API changes.

### Attribute insertion conflicts with native evidence, issue 32

The fixed-width child budget path from issue 4 already avoids sibling reads. Reuse
public insert_child and the repository's process rendezvous convention. Compare
compact independent parents with identical declarations whose padding separates
native data blocks. Hold two transactions after their count reads, commit one, then
release the other. Record attempt counts and logical read/write intersections, plus
read-only DSE FIND results for counts, metadata, and schema keys before/after. Capture
native GVSTAT and TP block-modification counters around each experiment. Separately
run four-process bursts on separate parents and one shared parent. Validate counts
and actual children after each run. Use only disposable native CI databases; never
assert a performance threshold or infer a block cause from retry counts alone.
Preserve raw diagnostics for the eventual report. No mapper algorithm change is
justified until these experiments identify a cause. Existing C901 passes at ten.

### Versioned ordered equality indexes, first slice of issue 15

Reuse FieldSpec, Definition/index_root, one Entry per field occurrence, uniqueness,
normal secondary replacement, occurrence checks, rebuilds, and named transforms.
Add explicit Field(ordered=True), implying an index and recording ordered-v1 in the
field's index contract. Keep all existing equality-v1 schema bytes and value keys.
Ordered fields use the same index/claim namespaces and entry identities, changing
only their value subscript. This preserves the shared index maintenance machinery.

Qualify native numeric keys for int/Decimal values with at most 18 significant digits
and nonzero magnitudes from 1E-18 through values below 1E18. Reject values outside
this initial domain before writes; never round or silently change collation. Dates
and UTC datetimes use their existing fixed ISO encodings. String keys retain a
nonnumeric prefix with order-preserving NUL/control-byte escaping; null sorts last.
Initially reject float, bool, reference, and list ordering. Equality indexes remain
available for their full existing domains. Compile choice-domain support explicitly.

A new ordered index on a previously unindexed scalar can reuse normal additive
migration. Changing an existing index's codec requires an explicit named transform,
which already clears, rebuilds, verifies, retags, and activates safely. Ordinary
rebuilds retain the installed codec, and review output must identify ordered codecs.
Test exact encoding/order goldens, domain boundaries and Decimal contexts, nulls,
Unicode/control strings, equality/uniqueness, patches, rollback/restarts, migration,
rebuild recovery, and real native collation. Only then add bounded range/prefix/sorted
and multi-predicate query plans; do not claim those APIs in this prerequisite slice.

First extract the existing field-index declaration checks from _field_spec without
changing behavior, keeping secondary declaration rules cohesive and C901 at ten.

Ordered numeric keys exposed an adapter gap: MemoryStore collated only canonical
integers numerically, so -.5 and 1.5 sorted as strings. The new shared-adapter golden
fails on memory before the fix. Recognize canonical fractional syntax within the
engine's 18-significant-digit/range domain using exact Decimal comparisons, keeping
noncanonical numeric-looking strings distinct. Existing str/int key encodings and
record bytes remain unchanged. Qualify the same golden on native CI before adding
ordered index declarations.

The shared-adapter fractional-key golden passed native CI 42336532015 before the
ordered declaration feature. Additive index domain failures preserve their normal
checkpoint/barrier and can be explicitly aborted before backfill; automatic abort
stays limited to validation-only migrations. Reusing named transforms for an existing
index codec conversion reapplies target field nodes even if their bytes are unchanged;
this is an explicit initial tradeoff instead of introducing a second conversion
executor. Rebuild reviews compare codec selections as well as fields/uniqueness.
The existing rebuild review's tuple uniqueness membership can become quadratic for
large declarations; no such overhead has been measured, so leave that cleanup out
of this codec change.

### Share concrete index validation before query expansion, issue 15

Existing occurrence projection validates an indexed owner and unique claim; root
equality validation checks exact occurrence sets but omits the corresponding unique
claim. Reproduce the missing-claim difference through public all/occurrences calls.
Centralize claim checking in indexes, reuse it for writes and both read shapes, and
separate indexed-owner reading from cursor/projection construction. Preserve equality
cursor bytes and bounded owner reads. This gives ordered scans the same deep read
seam without a parallel integrity implementation. Add the deterministic regression
before fixing it; existing C901 passes at ten.

### Bounded ordered query walks, issue 15

Reuse ordered-v1 value keys, Definition field adapters, native Store.children seeks,
occurrences.entry_page/read_indexed, and existing root materialization/checks. Add
range(field, gt/ge/lt/le), prefix(field, text), and ascending order_by(field). They
require an ordered index and never scan the data global. Numeric/date ranges exclude
null; sorting includes null last. Prefixes are literal stored-string prefixes, not
complete field values, so length/pattern validators and normalization do not apply.
Range endpoints are complete declared values and use the existing field adapter.

Walk native value subscripts, then owner keys and concrete digests. Bound every call
to 1000 combined value/occurrence visits and expose that bound through explain.
Version-two, schema/query-scoped occurrence cursors carry the last value, root key,
and digest. Preserve all equality cursor bytes and key-based pagination. Ordered
all() returns distinct roots ranked by their first matching occurrence, with no
key-based continuation; occurrences() provides resumable concrete owner pages.
This avoids a misleading root-key cursor across differently ordered value groups or
an unbounded cross-page duplicate set. Descending traversal and root-page cursors
are deferred; no reverse in-memory sort masquerades as an index seek. Follow with
bounded multi-predicate intersections as a separate slice. Existing C901 passes at ten.

### Bounded predicate intersections, issue 15

Extend immutable Query selections to at most eight indexed/key predicates. Preserve
single-predicate execution and cursor formats. Reuse the first selection as the
candidate index (or an explicit ascending order_by index), probe other equality
indexes at each exact candidate key, and walk other ordered selections under one
shared 1000-visit budget. Read and validate complete returned roots only after every
index has a witness, using existing validate_root and read_indexed checks. No data
global is enumerated. Explain lists each predicate and the candidate/order field,
without values. Owned predicates are independent existential matches under a root;
they do not imply that one child satisfies both. Reject duplicate field predicates
and ambiguous multi-predicate occurrence projection initially. Equality-anchor root
paging retains native key cursors; ordered anchors retain their explicit no-root-
cursor limitation. This avoids creating another cursor contract or implicit joins.

### Plain-M writer experiments before the contract, issue 25

Reuse disposable native CI, M subprocess execution, Path, and transaction rollback
checks. Prototype triggers on a small tagged field/index pair: SET maintenance,
leaf KILL maintenance, rollback, and parent KILL. Record whether the field trigger
sees a complete record during creation. Keep trigger names/global prefixes confined
to the test database and remove the definitions in finally. This tests specific
engine semantics instead of assuming that a leaf trigger protects every subtree.
Compare with an explicit M routine that validates a complete fixed input before
changing a record and its index in one owned transaction. These are experiments,
not an enabled production writer API. Inspect native results before choosing the
supported writer path or promising stored rollups. Existing C901 passes at ten.

### Initial generated M writer, issue 25

The native prototypes passed their first full runs on both engines. Implement ADR
0013's conservative subset by reusing compiled ModelSpec/NodeSpec, canonical schema
rules, metadata(), and normal index entry constants. A small source generator and
fixed M routine template form one export boundary, not a schema compiler framework.
Database.m_routine returns source bound to the currently verified canonical/legacy
identity without writing metadata. The M routine checks exact live schema bytes,
handles empty-global first installation transactionally, and fails closed on models
whose validation/ownership/index contracts it cannot implement. ASCII str/int with
length/numeric bounds is the initial M value domain; Python's domain is unchanged.
Test canonical byte compatibility, unsupported declarations/values, CRUD/indexes,
unique failures, metadata corruption, maintenance, native error rollback, restarts,
and two-process contention. No installed trigger is part of the supported API.

### Read-only projections of existing globals, first slice of issue 26

Reuse Model/Registry's field and complete-model validation, Store transactions,
Path validation, and existing query-page limits. Add GlobalView and MappedField as
an explicit read-only projection of trusted native paths, with caller-supplied pure
decoders. Each model field maps to a relative native node; several fields may decode
pieces of the same node. Native untagged keys use the declared str/int primary-key
type. get and bounded scan never install metadata, indexes, or rewrite source data.
Require flat, unindexed projection declarations and complete field mappings; validate
the complete returned object and exact primary-key identity. Defaults apply only
when a mapped node is absent, through normal Pydantic validation. This keeps foreign
storage interpretation separate from managed CRUD/adoption instead of fabricating
an installed schema or an adapter that silently emulates the managed layout.

A view does not promise ownership/reference/index integrity for foreign writers.
Unknown extra source nodes are intentionally outside the projection. There is no
write method or implicit migration. Qualify a sample produced by actual plain M,
native key order, shared-node decoding, missing/invalid input, and no writes. A
resumable managed-layout adoption job remains the separate second slice of #26.

### Resumable layout-one adoption, second slice of issue 26

Reuse adoption's exact-byte record audit, maintenance ownership/checkpoints, pinned
snapshots, migration uniqueness reservations, ordinary index/reference transitions,
and bounded terminal pruning. Keep adopt()'s existing single-transaction limits.
Add explicit begin_adoption/advance_adoption calls for larger exact-layout globals,
with validate/backfill/verify/activate phases and 1–100 records per batch. No foreign
format conversion is implicit; GlobalView remains the read-only mapping boundary.

Initial adoption has no installed source. Represent source_fingerprint=None only in
a new checkpoint format two, with a hashed adoption operation and target snapshot.
Existing jobs retain format-one bytes. Old clients reject the unknown new checkpoint;
new clients read both. Publish only a maintenance marker during the job, with no
installed snapshot until final activation. Inspection reports unmanaged/uninstalled
plus the job id, and ordinary access stays blocked for the adopted model. Retain
shared gate/ownership, completion, and pruning paths rather than a second job store.

Validate exact field bytes, pin each record's field digest, reserve all unique claims,
and check reference targets before backfill. In bounded backfill batches revalidate
pinned data/claims and target liveness, install record metadata and normal secondary
structures atomically, then verify them before activation. Self-references may target
other exact-layout records in the same adopted global without requiring premature
installed metadata. Existing managed reference targets retain normal metadata checks.
Framework writes are blocked; raw writers must be stopped, as for existing migrations.

Abort restores completely absent installed metadata before any backfill commits.
After committed metadata/index/reference writes, require resume; do not advertise a
rollback that would need to reconstruct arbitrary former state. Counts/digests detect
changed/deleted/new records during the supported stopped-writer lifecycle. Add fault
injection, reopening on every batch, wrong-executor/unknown-checkpoint rejection,
uniqueness/reference failures, native restarts, and concurrent initialization tests.
First extract the existing digest and adoption preflight/read seams without changing
behavior. Current C901 passes at ten; preserve all existing migration/adoption tests.

### Shared secondary transitions before stored rollups, issue 17

Search found ordinary patch/child mutations using mutations.replace_secondary, while
patch groups and policy deletions independently compose the same reference/index
unions. Stored totals need one final delta per surviving root, including grouped
changes and cascade-deleted children. Adding a sum callback to each caller would
spread the invariant across three paths. First extract one secondary transition
module that accepts explicit before/after field occurrences and deleted root paths.
Keep existing validation, unique-claim unions, reference restrictions, node-budget
checks, physical writes, and transaction boundaries. This is an internal seam only;
public APIs and bytes remain unchanged. Then extend that seam with root-local stored
sum preparation. Existing Ruff/C901 passes at ten; the long deletion apply method
is a cohesion cleanup candidate addressed by removing its repeated secondary logic.

### Root-local stored Decimal sums, issue 17

Add Field(sum_of="locations.bins.balances.quantity") for a root Decimal field, with
an implicit Decimal(0) default. Compile its source field identity through declared
ownership; require at least one child collection, a nonnullable Decimal source,
and local validation on root/ancestor owners. Leaf validators retain ordinary full
owner validation. Reject owned stored sums, reference traversal, dependency cycles,
custom root/ancestor validation, and unsupported target declarations initially.
Virtual Pydantic computed fields remain unchanged.

Record sum_of and decimal-sum-v1 in the canonical contract; unchanged models keep
all prior snapshot bytes. Source values use up to 18 significant and 18 fractional
digits with magnitude below 1E18. Fixed 1E-18 units bound exact integer arithmetic
independently of Decimal context and prevent enormous exponent expansion. Aggregate
units are bounded to 40 digits by the existing record-size domain. Reject unsupported
inputs before writes; never round. Stored totals are read-only to patch callers.

Whole writes validate the aggregate normally, derive the sum from final validated
values, and validate its target adapter without rerunning arbitrary leaf callbacks.
Stored reads validate the complete model and verify any sum fields whenever that
owner is loaded. Partial operations feed their existing before/after field events
through secondary.replace, combine all deltas per surviving root, read/write one
stored total, and union its ordinary index transitions with the source changes.
Do not load siblings or introduce a separate rollup index implementation. Deleted
whole roots need no surviving total; cascaded child deletion and set_null validators
that alter quantities contribute to surviving totals normally.

Add/change/remove dependencies only through a named record transform. Transform
outputs derive target totals before pinning fields/indexes. Direct/adoption/rebuild
and migration reads verify managed totals; no default-zero additive backfill is safe.
Update the inventory warehouse to schema revision two with frozen v1 owned declarations
and an explicit transform, keeping its virtual total for comparison. Generated M
writers reject these models, as ADR 0013 specifies; raw SET/KILL remain unsupported.

Native tests must prove restart/rollback behavior, independent-child concurrent
updates, indexed-total/unique failures and group exchanges, and parent/child deletes.
A shared stored total is an intentional contention point; do not promise independent
commits for writers that update it. Test direct IO bounds as unrelated siblings grow,
complete validation, numeric boundaries/contexts, schema goldens, and explicit upgrade.

The stored-sum core passed native CI 42344716067 on both engines and journaling modes,
including separate/same-child writers, unique-total contention, parent delete races,
forced restarts, and late failures. The inventory application then adds stored_on_hand
at warehouse revision two. Freeze the original warehouse ownership graph and compare
it against the pre-refactor golden; do not overwrite that fixture. A named transform
initializes real totals and preserves incoming reference identities. Existing-balance
moves reuse patch_many to eliminate net-zero total/index IO, demonstrated by trace.

The quantity-node-only benchmark must not corrupt a v2 total by omitting its update.
Keep this intentionally weaker lower bound on the frozen v1 warehouse, retaining a
valid dataset and all existing balance/index/reference checks. Label each output row
with warehouse_schema_version and describe the semantic difference; current partial
and replacement workflows both use v2. No old latency report is presented as v2 data.

Inventory CI 42345725275 passed both engines, both journaling modes, full native
suites (2,150 passed per configuration), separate stress checks, demos, and benchmarks.
Commit the four structured benchmark runs with their actual source identity and
raw samples. Document current node-difference replacements separately from the
older benchmark, and state that the v1 quantity-only baseline omits v2 guarantees.
Refresh the limits and current architecture notes where their earlier adoption,
M-writer, ordered-index, and stored-computation descriptions became stale. No
runtime contract changes are needed in this qualification/documentation slice.

### Reviewed follow-ups 36–46 and scoped M maintenance, issue 40

The approved order starts with scoped maintenance, caller-owned M transactions,
Decimal codecs, references, and hierarchical writers. Typed queries and bounded
reverse/root pagination can ship independently. Owned sums, container support,
revisions, and concurrent jobs require the reviewed design or measurement gates.
The last two may end with explicit deferral rather than speculative machinery.

For issue 40, reuse the gate, format-one/two checkpoints, and schema barrier from
maintenance.require_available. Generate a bounded reader of the existing flat,
canonical checkpoint grammar in the M template, not a second job lifecycle or a
process-local scope cache. Validate the complete checkpoint and barrier in the
write transaction before permitting unrelated globals. Unknown formats, malformed
values, terminal jobs, and missing ownership fail closed. Existing exported source
keeps its conservative gate check until regenerated. No persisted bytes or public
Python signatures change. Root/reference maintenance will reuse this M check.

Check source export without mutation, both checkpoint formats and operations,
corruption, abort/resume, forced restarts, and coordinated unrelated writers against
actual YottaDB. Existing C901 passes with the configured maximum of ten; no Python
function needs a higher limit. Run full local quality/packaging checks and qualify
both supported native engines in CI before closing the issue.

### Caller-owned M transactions, issue 39

The existing writer owns a TP, validates complete input, then invokes attempt with
fresh database-derived locals after every restart. Python atomic marks failed writes
rollback-only. Reuse that M attempt and preflight; do not add a parallel index or
transaction framework. First extract preparation without changing existing behavior
and commit that refactor independently.

Add inserttx/replacetx/deletetx entry points for an existing caller transaction.
A missing transaction returns transaction without writing. Any validation/status
failure in a participant aborts the entire active transaction, as does an unexpected
engine error before propagation. This explicit abort policy prevents committing a
successful first operation after a failed second one. A successful participant
leaves the caller's transaction open and never commits it. Existing entry points
continue owning one TP and returning nested without changing the caller's TP.

The caller owns the outer restart point, must recompute database-derived values
inside it, and must avoid external effects in retryable code. Prove two globals
commit together, late uniqueness and invalid-input failures leave neither update,
unexpected errors abort, and forced/concurrent restarts recompute values. No record,
index, schema, or Python signature changes. This deliberately covers flat models
before the independent scalar/reference/hierarchy extensions.


### Exact M Decimal scalars, first slice of issue 36

Reuse the canonical Decimal field constraints, record encoding, and equality-index
normalization. Factor the existing exact fixed-unit conversion out of rollups before
using it to compile M bounds. Its bytes, numeric limits, and sum behavior do not
change. The M wire subset is canonical finite Decimal text exactly representable
in 1E-18 units with at most 40 unit digits, sharing the established rollup domain.
Python retains its full supported Decimal domain. Do not use native floating
arithmetic for coefficient comparisons, normalization, or bounds. Reject unsupported
constraints at export and invalid/out-of-domain inputs before writes. Preserve raw
canonical scale in record nodes and normalize equality tokens independently.

Compare M/Python field validation at numeric boundaries, signed and scaled zero,
precision/place constraints, and multiple Decimal contexts. Reuse the common M
preflight for both transaction-owning and participating calls. Qualify literal
record/index bytes, duplicate claims, old out-of-subset records, restart, and late
rollback. Add more scalar types in subsequent demonstrated slices, not all at once.


M Decimal precision/place limits apply to the encoded scale directly. Pydantic can
also accept a value after normalize(), which depends on its ambient Decimal
context. The M input subset deliberately requires the raw tuple to satisfy these
limits, guaranteeing acceptance by Python without emulating a process-specific
context. Applications can supply a shorter equivalent scale when appropriate.
This restriction is explicit in the M wire contract; Python validation and stored
codecs do not change. Unconstrained M Decimal fields preserve any canonical scale
within the node bound when its value fits the fixed-unit domain.

The additional M helpers expose a preexisting exporter name collision: a routine
name equal to the initial label and another emitted entry cannot compile. Use a
private initial label only when needed, keeping filenames/callable entry points
compatible. The template loader derives collisions from actual labels; new shared
helpers must not reserve previously valid routine names. Native tests compile and
execute these names rather than merely checking a text guard.

### Managed reference fields in M, issue 37

Reuse FieldSpec.target, exact registered target bindings, metadata(), and the existing
reference entry/count layout. Export reads and binds each referenced target's live
schema identity without converting metadata. A changed target identity requires
regeneration even when its global/key identity leaves the source snapshot unchanged.
Read target fingerprints/snapshots and record metadata in the same TP as link writes.
Use the existing scoped maintenance check for each target; removals may still release
links to a migrating target, as Python permits.

M callers supply the target's plain key. Compile its declared str/int key codec and
encode r:s:key or r:integer exactly; retain the existing ASCII/int-key M subset.
Reuse ordinary equality/unique indexing. Reverse locations and digests come from the
same canonical field path, including unindexed references. Validate old edges and
canonical counts, group changes per target, and add/subtract counts as bounded digit
strings rather than rounding with M numeric arithmetic. Unchanged edges stay intact.
Check new target existence after staging source nodes so self-reference insertion
can see its own managed record. Any failure rolls back data, metadata, claims,
reverse edges, and counts through both existing TP-owning and participating paths.

Deletes keep conservative restrict behavior rather than executing Python set_null
or cascade. Validate inbound count/edge presence; a valid zero-count namespace must
not permanently prevent deletion. Target metadata reads and count writes ensure a
concurrent Python delete either loses its restriction check or causes the M writer
to restart and reject a missing target. Test both orders, missing/corrupt targets,
reference replacement/deletion, self-reference, large exact counts, legacy schema
bindings, restart, rollback, and multi-model transaction composition on native.
No persistent layout, source reference identity, or public Python signature changes.

### Typed query fields, issue 43

Reuse selectors._Probe/_Leaf and the immutable schema binding used by ModelPath.
Database.field selects one declared scalar/reference; QueryField.eq produces a
typed equality predicate consumed by the existing Query._equality and intersection
planner. Query.where keeps its keyword form and accepts these predicates as
positional arguments. order_by also accepts a typed field. No new query executor,
indexes, model mutation, or persisted contract is needed.

Declaration paths differ from concrete owned addresses: each(collection) explicitly
selects the child declaration, while keyed access is rejected in query selectors.
Share probes with an explicit declaration mode rather than duplicating traversal.
Bind fields to their root class and fingerprint, validate their index through the
existing planner, and reject computed selectors without reading storage. Strict
mypy probes must demonstrate field typos, wrong values, and wrong root types; native
and memory tests compare keyword and typed queries. Existing touched functions pass
the configured C901 maximum of ten; keep that threshold unchanged.

### Container decisions and constrained list items, issue 44

Scalar lists already validate through the owner's field adapter, encode individual
tagged nodes, and include their item schema in migration validation comparisons.
Accept Annotated scalar items by extracting their FieldInfo once through a shared
scalar_lists helper. Reuse canonical rule compilation for their constraints; retain
scalar-list-v1 bytes, bounds, equality semantics, and existing plain-list snapshots.
Handle constraints inside and outside nullable item declarations consistently.
No item codec or parallel validation path is needed.

Keep nested lists, arbitrary maps, model/reference lists, and ambiguous unions
rejected. Children with a declared wrapper model supplies keyed identity, ownership,
validation, and reference semantics for concrete workloads. Record each decision in
an ADR and improve rejection messages with that alternative. A new constrained item
changes canonical metadata and requires explicit migration; test both validation
failure recovery and a named correcting transform. Add a golden fixture and qualify
it on both supported Pydantic versions. Existing touched functions pass C901 <= 10.

### Detached concurrency decisions, issue 45

Review Inventory.receive/move/adjust, existing native detached/atomic rendezvous
tests, replacement node diffs, and generated M transaction participants. The sample
uses retry-local reads and patch groups, with no detached editing workflow. Keep
save's intentional last-writer-wins contract. Update ADR 0003 with separate decisions
to defer optional revisions and automatic tracking, their evidence, and concrete
reconsideration criteria. A root revision would add contention to independent child
writes and needs a managed-writer activation protocol; tracking would add mutable
object lifecycle without removing validation dependencies. No API, stored metadata,
or code changes are justified by this workload. Re-run the existing concurrency
regressions and documentation checks; complexity is unchanged.

### Stored sums on owned records, issue 41

Reuse rollups' compiled source identities, exact decimal units, shared secondary
changes, and ordinary indexed field occurrences. Compile owned declarations bottom
up, requiring local validation on every ancestor of a sum. Keep sources authoritative
Decimal leaves; reject sums of other stored sums rather than introducing a dependency
compiler. Whole writes and child insertion initialize owned totals after validation;
loaded owners verify them recursively.

Group partial deltas by root and concrete total path, using each declaration's
physical ownership depth to identify the affected owner. Read/write each nonzero
surviving total once and pass its ordinary field occurrence through existing index
maintenance. Newly inserted owners already contain initialized totals; deleted owners
need only release their total indexes, while surviving ancestors receive deltas.
Net-zero patch groups touch neither their totals nor indexes. No sibling reads or
new storage codec. Owned sums require explicit transform migration; preserve frozen
warehouse-v2 declarations before upgrading the inventory location example. Compare
native contention for no sums, root sums, and root plus location sums before claiming
scalability. Current C901 passes; keep the maximum of ten.

The owned-sum contention benchmark reuses CountedStore, latency/environment output,
and public atomic/patch calls. The existing contention launcher dispatches its
older fixed workload script, so retain a small benchmark-local spawn/barrier runner
instead of introducing a generic job framework. Compare two independent child
writers within/across locations over three repeats and three indexed-total layouts.
Preserve all samples, attempt/retry counts, adapter calls, and final integrity checks.
Run from the disposable native harness in all engine/journal CI configurations;
offline tests verify logical workload equivalence only.

### Reverse traversal and ordered root continuation, issue 42

First add an optional reverse flag to the existing Store.children seam. Native
uses YDBPython subscript_previous through the same signal/error wrapper; memory
reverses its existing numeric/string collation. Keep default forward calls compatible
and update instrumentation/read-only wrappers. Qualify absent cursors, limits,
numeric/string boundaries, and restart propagation before changing query execution.

Then thread direction through ordered selections, occurrence walks, explain, and
cursor scope. Descending is the literal reverse, including root/digest ties and nulls
(last ascending, first descending). Root continuation uses a canonical first matching
occurrence per root in the chosen direction, computed from a bounded loaded aggregate;
do not carry an unbounded seen set between pages. Reuse existing record/index validation
and the shared 1,000-visit budget. A dedicated ordered page result exposes records and
its cursor; all() keeps its list return. Cursor identity includes schema, direction,
and predicates; exactly-once pagination is promised only for a stable dataset.
Combined occurrence projections remain ambiguous and rejected. Existing touched
functions pass C901 <= 10; do not increase the threshold.

Root pages need bounded progress even when skipped duplicate occurrences consume
the whole visit budget. page() may return no records with a non-null continuation
cursor; None means completion. The cursor advances only past fully checked positions.
If not even one candidate can be evaluated, raise rather than repeat the same cursor
forever. all() retains strict budget failure instead of returning a truncated list.
This avoids an unbounded cross-page seen set and makes large duplicate groups
pageable without weakening the configured visit limit.

The concrete newest-movement report needs an ordered occurred_at index. Advance
Movement to application revision three with the existing additive index migration,
preserving record bytes. Freeze MovementV2 and retain the original UTC-v2 named
transform for recovery. A distinct UTC-ordered-v3 transform handles direct v1-to-v3
conversion. Test resuming old v2 jobs before adding the index, timestamp/key ties,
typed sorting, scoped root pages, and unchanged stock/reference data.

### Measure maintenance serialization before changing its lock protocol, issue 46

Reuse the actual WarehouseV2-to-current named-transform upgrade, scoped maintenance
gate, index-rebuild planner, public job resume APIs, and benchmark counters. Measure one
attempted unrelated rebuild while a warehouse job is active, unrelated control-model
CRUD latency, remaining active-job duration, and the second job after release. Use
1/10/50 warehouses with two locations and ten lots per location, small committed
batches, and both native engine/journal configurations. The API refuses a second job
immediately; it does not queue it, so remaining job duration is an operator scheduling
delay bound for this controlled workload, not an observed production waiting time. Do
not infer a deployment requirement or implement per-model locks from synthetic results
alone. Update ADR 0009 with the measured decision and the conflict/reference, recovery,
activation, and older-client requirements of any future concurrency design.

Use an independent control model with no references to/from the inventory graph.
Product is connected through balances and is not evidence for disjoint-job behavior;
do not use the original connected-model probe as unrelated-job qualification.

### Owned-path M writers and exact sums, issue 38

Reuse generated scalar validation, schema/record bindings, TP ownership and participant
policy, ordinary index claims, reference transitions, and decimal-sum-v1 source/total
domains. First separate owner declarations from global bindings in the exporter and
share the flat preflight/index transition without behavior changes, in its own commit.
Then export keyed ownership trees whose nodes use the qualified scalar/reference
subset and local validation. Continue rejecting embedded/nullable containers and
custom validators, including SupplierProduct; do not translate Python callbacks.

Add insertchild(root_key, collection, input), patchchild(root_key, at, changes), and
deletechild(root_key, at), plus explicit tx participants. M path arrays alternate
declared collection names and plain keys, matching Python addresses. Initial operations
target leaf children under an already managed root; Python constructs/replaces the
aggregate. Existing whole-record M labels refuse hierarchical roots explicitly rather
than store incomplete records. Flat routines keep their existing behavior.

Resolve and verify root metadata, schema, ancestor o:1/c:N markers, and identities
inside each attempt. Address only the leaf, changed secondary entries, and affected
ancestor totals. Reuse record_budget's fixed-width-count approach for insertion;
only variable-width structural branches require bounded traversal. Enforce collection,
path, node, and numeric limits before persistent changes. Unchanged references do not
resolve targets or touch reverse counts during scalar patches. Maintain dotted index
names, concrete locations, reference edges/counts, and all surviving indexed totals in
the same TP. Any owning or participating failure rolls back all work.

Concrete owned locations require the existing SHA-256 digest of canonical ASCII JSON.
Precomputed flat digests cannot cover arbitrary child keys. Avoid a new plugin/native
dependency or storage identity: implement bounded SHA-256 using YottaDB byte/bit
intrinsics and exact 32-bit arithmetic, qualified against hashlib and standard vectors.
Use bounded signed decimal digit arithmetic for up-to-40-digit fixed-unit sum changes;
native 18-digit arithmetic cannot represent the contract. Qualify primitives separately
before integrating mutations. The algorithms follow
[FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/final) and documented
[YottaDB bit operations](https://docs.yottadb.com/ProgrammersGuide/functions.html).

Test physical Python/M equivalence, direct quantity deltas without sibling traversal,
capacity/count limits, target/ref/unique failures, self-contained tx composition,
forced restarts, late rollback, concurrent Python writers and deletion races on both
engines/journal modes. Preserve old schema/layout bytes; regenerate source for new
capabilities. Existing C901 passes at ten; keep that threshold unchanged.

Keep shared constant generation in m_writer_config, reused by flat and hierarchical
exporters. This separates owner declarations from installed global bindings without
creating fake ModelSpecs or circular module imports. Existing flat, Decimal, and
reference exports must remain byte-for-byte identical through this extraction.

Before reference support, share schema constants and M schema/record-metadata checks
without changing their validation or stored bytes. This keeps source and referenced
target bindings on the same implementation, including exact legacy snapshots.

### Bind descending range cursors to both bounds, issue 52

Reuse Selection, index_scans.identity, decode_cursor, and the root query scope built
from that identity. The descending start position contains the upper bound, leaving
the lower bound outside the existing scope. Extend only descending range identities
with the lower value and inclusivity, including an absent lower value. This rejects
all ambiguous legacy descending range tokens. Ascending, descending ordered, and
descending prefix identities remain byte-compatible. Stored schemas and records do
not change. Document the one-time cursor invalidation.

Capture old public cursor tokens before the fix, then test root and occurrence
projections against changed lower values, inclusivity, and unbounded selections.
The committed fixture also protects unaffected cursor bytes. Both storage adapters
exercise the same tests. The configured C901 check passes at ten before this change;
no threshold changes or unrelated cleanup are needed.

### Separate stored readability from M input acceptance, issue 49

Reuse old(), valid(), the Decimal parser and exact unit comparisons, and the existing
UTF-8/natural-number helpers. old() currently applies the input subset to stored
values and labels every refusal corrupt. Add one stored-value classification seam:
definite syntax, tag, shape, or enforceable constraint violations remain corrupt;
well-formed values outside the qualified M domain return unsupported. Unsupported
means M cannot validate or manipulate the value, not that Python has certified it.

For supported finite Decimals, validate stored precision using the exact coefficient
with trailing zeros removed, treating every signed/scaled zero as zero. Keep raw
payloads intact so unrelated patches preserve scale. Incoming inputs retain the
conservative raw precision contract and all existing rejection tests. A parser
marker distinguishes valid Decimal syntax outside the fixed-unit domain from
malformed text. Reuse the stored check for existing sum targets. No schema/layout
conversion or Python validation change is involved.

Native tests cover all reported Decimal forms, flat and owned writes, unchanged
payload bytes, unsupported Unicode/wide integers/Decimals, malformed values, and
rollback. Compare normal queries/indexes and Python mutations after successful M
writes. C901 remains ten; no unrelated cleanup is proposed.

### Document and qualify restartable caller transactions, issue 50

Reuse stableabort and the existing participant drivers. YottaDB takes restartability
from the outermost TSTART, so a nested TSTART () cannot enable it for a bare outer
TSTART. Preserve stable read revalidation instead of returning potentially stale
failure statuses. The qualified caller interface requires TSTART () or TSTART *;
SERIAL is optional. No unqualified engine introspection or new status guard is added.

Add native cases for plain and nested nonrestartable callers, a prior successful
participant followed by invalid input or a duplicate, and caller error-trap cleanup.
An engine restart refusal can reach the caller with an active transaction level even
after staged data is restored. Require the caller's error handler to TROLLBACK before
continuing. Verify data, schema, and indexes independently after the expected error.
Also qualify a restartable outer TSTART () without SERIAL. This is a documented
precondition and regression coverage, with no stored-format or writer-code change.

### Make ordered result limits and work limits explicit, issue 51

Reuse Budget, strict ordered_page execution, RootPage progress cursors, and the
existing explain fields. A result limit is a ceiling and cannot guarantee a number
of matches under bounded execution: rejected predicates and repeated owned witnesses
can exhaust the budget even for one requested result. Preserve both limits and all
visit accounting. Add an actionable strict-all diagnostic that names the requested
ceiling and directs ordered callers to page() with continuation. Other visit-limit
paths keep their existing errors, including a page that cannot fit one candidate.

Document the two visits per distinct value/root and the approximately 500-result
case without describing 500 as a universal safe limit. Regressions cover 700 distinct
roots, full continuation, and a filtered query whose first match lies beyond the
budget. They use both existing adapters. No new budget setting or query interface is
needed. C901 passes at ten before the change and the threshold remains unchanged.

### Bound repeated M location hashing, issue 47

Reuse sha256, hwhere, the existing inventory declarations, native test driver, and
benchmark environment/latency helpers. Keep SHA-256 and every stored digest byte.
Memoize only the exact canonical location string in a reserved process-local
%YHMFDigestCache namespace. Bound it to 128 entries of at most 4,096 bytes each and
clear the entries at capacity. A format marker separates its contents from future
algorithms. Never cache schema, record, index, reference, count, or transaction state.
Pure digest entries remain correct through TP restarts without database invalidation.

Qualify cache hits, eviction, byte equality, and restart behavior on native. Compare
the production writer with an otherwise identical exported control that bypasses
only memoization. Measure insert/patch/delete separately, warm paths, cleared-cache
paths, and a working set larger than the cache. Instrument hash calls and TP attempts
with process-local counters, not persistent writes. Include one process and four
processes with separate products versus a shared product to expose reference-count
and block contention. Keep setup, compilation, and coordinated start outside timing.
Verify quantities, totals, indexes, and reverse references after each case. Report
raw samples and environment with native r2.02/r2.06, journaling off/on. A C call-out
is deferred unless the cold-path measurements justify its added dependency.

### Qualify finite M choice domains, issue 55

Reuse Choices, canonical scalar encoding, the shared field compiler, valid(), old(),
and existing equality/unique index maintenance. Accept string and integer Enum and
Literal declarations by compiling their exact primitive domain alongside the existing
scalar rules. Runtime membership uses the same escaped scalar JSON as equality tokens;
chunk constants to keep generated M lines bounded, including controls and long options.
The field compiler remains the single validation seam for flat and owned writers.

Bound each exported domain to 256 members and 32,764 encoded bytes. Reject the whole
export for Unicode, wider integers, booleans, nullable choices, unsupported constraints,
or custom validators until those domains are separately qualified. Membership does not
call Python Enum fallback hooks. No schema, record, index, or fingerprint bytes change;
applications regenerate source to use the additional writer capability. Preserve
existing Python choice behavior. Test byte equality, membership failures, unique
rollback, stored invalid members, escaping, and export bounds on both native engines.
Configured C901 passes at ten before this change; no threshold increase or unrelated
cleanup is needed.

### Publish native cache evidence, issue 47

Preserve the four successful CI reports, their exact source identity, environment,
raw samples, operation counters, retries, and cache bounds. Reuse performance.md
and the existing benchmark archive. Report hot, cold, churn, Python controls, and
shared/separate Product bursts without claiming that digest caching removes conflicts
or improves cold hashing. The native matrix passed both engines, both journaling
modes, and stress. No production change or additional abstraction is needed.

### Qualify canonical M temporal scalars, issue 55

Reuse temporal-v1 calendar/UTC codecs, canonical temporal bounds, scalar tags,
quote/token encoding, old(), and existing transactional index maintenance. Add a
small calendar validator for Gregorian years 0001–9999 and canonical UTC timestamps
with exactly six fractional digits. Compare canonical temporal bounds lexically,
without converting dates/timestamps to native numbers. M accepts canonical wire
text only; Python retains its aware-offset normalization. Reject nullable, ordered,
custom-validator, or additional temporal rules until separately qualified.

Both flat and owned compilation use the same scalar rules. No schema, record, or
index conversion occurs. Differential tests cover calendar extremes, leap centuries,
UTC precision, exact bounds, unique rollback, existing Python offsets, and malformed
stored values. Add generated valid date/instant cases. Configured C901 passes at ten
before implementation; keep that threshold and add no alternative persistence codec.

### Qualify explicit nullable M scalars, issue 55

Reuse the shared field compiler, scalar codecs, input/old readers, and index claim
transitions. Distinguish null structurally: input(field,"%null")=1 with no scalar
value or other descendants. Ordinary scalar input remains unchanged, including empty
strings and literal "n:". Internally tag nullable wire values before persistence;
unwrap only at codec boundaries. Missing required input stays invalid and Python
factories/defaults are not evaluated. Stored null stays exactly n:.

Compile nullable forms of already qualified scalars and finite choices. References,
containers, lists, and unqualified scalar families remain rejected. Null has ordinary
equality entries but no unique claim, matching indexes.entries; multiple nulls are
allowed. Test null/value transitions, exact nodes/indexes, uniqueness rollback,
ambiguous-looking strings, malformed null shapes, and owned patches/restarts. There
is no stored-format change. Configured C901 passes at ten before this change; extend
existing seams without another index or nullable serialization subsystem.

### Patch scalars on roots and non-leaf owners, issue 53

Reuse owned(), hprepare/hresolve, attempt(), old(), and the existing index/reference
transitions. Add patch(key,input) and patchtx(key,input) for roots; allow patchchild
on non-leaf owners as the same operation at a concrete path. Normalize flat writer
constants into the existing node configuration for this path. An empty internal path
addresses the root; insert/delete retain their current path/operation restrictions.

old() recognizes declared immediate child collection markers/count presence without
walking their contents. Reject unknown fields and malformed/missing immediate shape;
read and validate owner scalars with the existing compiled rules. Root patches retain
root metadata rather than owned markers. Only changed indexes/references are updated;
child subtrees, their indexes, counts, and sums remain untouched. Derived sums stay
read-only. This introduces no aggregate replacement shortcut or stored-format change.

Test physical equivalence with Python patch, unchanged children, invalid root shapes,
unique rollback, participants, and a forced restart after index writes. Complete tree
creation/deletion remains a later issue-53 slice. Configured C901 passes at ten before
this change; no unrelated Python refactor or threshold change is required.

### Qualify native UTF-8 string writes, issue 55

Reuse the existing strict UTF-8 parser, scalar/key bytes, canonical_json, quote/token,
and shared field/reference validation. Factor one byte-oriented codepoint decoder
under UTF-8 validation and JSON quoting, independent of the M process character set.
Count Unicode codepoints for string constraints, preserve UTF-8 record/key bytes,
and emit Python-compatible ASCII JSON escapes including surrogate pairs. Existing
ASCII quoting and digest identities must remain byte-identical.

Qualify strings, finite string choices, and string reference keys within the existing
byte/key budgets. Reject invalid UTF-8 before character-sensitive reads; use byte
operations to peel storage/internal wire tags. Keep unsupported patterns and validators
rejected. No Unicode normalization is performed. Tests compare full Python nodes,
index/claim bytes, Unicode ownership digests, malformed input/storage, character limits,
and generated strings under M and UTF-8 process modes on both engines. Remove only
the superseded ASCII refusal checks/locals. Configured C901 passes at ten before
this change; no threshold changes or native dependencies are needed.

### Create and remove empty non-leaf children, issue 53

Reuse hprepare, capacity/count checks, attempt, old, and ordinary index/reference/sum
transitions. A non-leaf insert supplies all non-derived scalar fields and implicitly
creates empty declared collections. Do not run application defaults/factories. Derive
empty stored totals as zero and validate them through the existing scalar rules.
Reject insertion when an empty collection violates its declared minimum. Never accept
caller-supplied counts, collections, or derived totals.

Permit non-leaf deletion only when every immediate collection is empty, after old()
checks its marker/count shape. Populated branches still return operation without
writes. This keeps bounded empty-branch operations separate from a future complete
subtree deletion plan and its aggregate constraints. Root insert/delete and complete
replace remain unchanged in this slice. Retain the leaf classification used by bounded
capacity traversal. Compare Python physical state, totals/indexes,
minimum-length refusal, and rollback on both native engines. C901 remains ten.

### Create and delete empty hierarchical roots, issue 53

Reuse hconfig to select a hierarchy's root declaration for ordinary insert/delete,
including schema metadata, reference bindings, and key limits. Keep replace rejected
for trees. The shared attempt path already initializes empty collections/zero totals
and refuses populated deletion. Its existing transaction checks require an empty
unmanaged global before first metadata installation; no separate initialization path
or adoption procedure is added. Empty-root deletion keeps incoming-reference checks.

Qualify exact Python node/index equivalence, raw records at same/different keys,
concurrent first writes with and without a unique collision, participant rollback
including initial metadata, and forced restarts. Root construction is empty only;
application defaults/factories and populated tree replacement remain unqualified.
Schema and storage bytes do not change. Configured C901 remains at ten.

### Write ordered-v1 indexes from M, issue 54

Reuse ordered.value_key's existing contract, the shared index-before/index-after
transitions, unique/null claim handling, exact Decimal parser, and UTF-8 scalar
validation. Add token dispatch for ordered-v1: native canonical numeric subscripts,
D:/T: temporal bytes, NUL/SOH-escaped UTF-8 strings, and z:null. Build numeric text
from normalized coefficient/power without native rounding or scientific subscripts.
Reuse sourceunits for the ordered Decimal domain (18 significant digits, 1E-18 to
below 1E18 for nonzero values). Preserve raw record text and unrelated indexes.

There is no new index namespace, persistence API, codec, or metadata conversion.
Export still rejects unsupported scalar declarations/validators. Differential tests
cover every qualified key family, null/unique transitions, failed claims, root and
owned patches, ordered queries, and generated exact numerics on both native engines.
The baseline C901 check passes at ten; retain the threshold and the current index seam.

### Persist nonnullable embedded objects from M, issue 54

Reuse owner declarations, input/old validation, attempt, hconfig/hwhere, and ordinary
index/reference transitions. Compile embedded owners as keyless nodes. Complete
record/owner writes validate nested M input arrays, then invoke the same attempt
logic recursively at each o:1 embedded address, inside the existing outer TP.
Concrete index names, locations, digests, root identities, and reverse edges retain
Python's layout. Partial scalar patches leave embedded subtrees untouched.

Start with nonnullable embedded objects without child collections inside them.
Include their fixed node widths in existing capacity accounting. Reject embedded
rollup sources until delta composition is qualified; reject custom validators and
unsupported declarations as before. Bound embedded declaration depth to 14 and keep
64 declarations/16 fields per owner. Nullable embedded objects are a separate slice.
Complete tree replacement remains rejected where Children are present. No extra
persistence/index layer, flattening representation, or storage conversion is added.
Native differential tests cover nested records, indexes/references, replacement,
partial preservation, malformed shapes, unique failure, rollback, and restart. The
configured C901 check passes at ten before implementation; keep that threshold.


### Address M input nodes without unbounded copies, issue 54

M does not accept a subscripted local as a by-reference actual argument. Extract the
existing input loop into inputnode, taking a trusted name built by $name while the
public input wrapper retains its original reference. Preserve all flat/owned scalar
and typed-null validation. This creates one traversal seam for nested validation
without copying an unvalidated subtree. Commit this behavior-preserving change before
embedded support, retaining the existing input API and wire values.

### Qualify nullable embedded object transitions, issue 54

Reuse nullable-rule normalization, typed-null input, embedded owner attempts, and
presence markers. Null objects accept the same exact %null child flag and persist
n: without nested indexes/edges. Object/null transitions delete or create the same
embedded owner within the outer transaction; failures/restarts restore both shapes.
Reject n: markers with descendants and missing physical fields.

Account for variable object presence exactly. Keep fixed-width leaf fast paths for
fixed declarations; variable owners traverse only structural object/count markers.
Compute incoming child width from validated wire presence rather than maximum width.
This prevents both over-capacity writes and false budget failures for mostly-null
objects. No metadata/data conversion or additional persistence layer is introduced.
Retain nonempty object, depth, child-collection, rollup, and validator restrictions.
Native tests cover exact nodes/indexes/edges, null/unique transitions, malformed
shapes, forced restarts, and variable-capacity boundaries. C901 stays at ten.

### Centralize exact stored-sum delta application, issue 53

Reuse hsums, decimal units, hwhere, and ordinary index transitions. Extract the
existing target read/validate/update block into happly, leaving source delta selection
and per-call behavior unchanged. This is the single seam that populated subtree
deletion will use to apply a retained ancestor's net delta once, rather than expose
intermediate totals or duplicate the sum/index algorithm. Commit this refactor before
delete behavior changes. Existing exact-total, unique rollback, and restart cases
cover it; configured C901 stays at ten.

### Delete populated M subtrees with net ancestor totals, issue 53

Reuse hresolve/hcapacity, old/attempt, embedded cleanup, index/reference transitions,
and happly. A bounded postorder traversal validates each removed owner, accumulates
source deltas for retained ancestors, and removes owners with the same delete path.
Apply each retained ancestor's net delta once after traversal. Sums inside the removed
subtree keep their original claims until their owner is deleted; they need no transient
recomputation. This avoids invalid intermediate totals and unique claims.

Check retained parent minimums and root incoming restrictions before writes. Relax
collection minimums only inside the subtree being removed, and suppress per-leaf sum
updates during that transaction. Keep original schema bindings and validation rules.
Validate concrete stored keys and observed child counts, bound traversal by the selected
subtree's 10,000-node budget, and avoid scanning unrelated branches. No cascade/set-null
reference policy or new deletion framework is introduced. Public delete/deletechild
participants retain their transaction/restart contracts. Whole tree replace stays
rejected. Test Python equivalence, net-zero mixed-sign sources, minimums, unique
failure, incoming references, concurrent quantity changes, rollback, and restart on
native.

### Freeze movement revision three before choice migration, issue 54

Reuse the existing frozen v1/v2 declarations, canonical schema fixtures, and named
Transform lifecycle. Add an independent MovementV3 binding with the current ordered
string-kind contract; verify its bytes against the committed v3 golden and active
model under both supported Pydantic versions. Do not retarget existing transforms or
change the active model in this prerequisite slice. The next slice will introduce
Literal kinds as revision four with a separately named transform, preserving old-job
recovery. Configured C901 remains at ten.

### Activate movement kind choices as revision four, issue 54

Reuse the frozen MovementV3 contract, finite choices, and named Transform planner/
recovery. Change the active kind field to Literal[receive, move, adjust] and advance
only the application's movement revision. Preserve the original UTC-v2 and ordered-v3
transforms and their frozen targets. Add a separate kinds-v4 transform and a separately
named direct v1-to-v4 timestamp/choice transform. Old jobs must finish against their
original bindings before further upgrades. Record/index/reference value bytes remain
unchanged for valid v3 rows; metadata evolution remains explicit and resumable.
Test old golden stability, distinct v4 output, every recovery phase, preserved values,
old timestamp recovery, and successful M export. C901 remains ten.

### Commit an inventory receipt from M, issue 54

Reuse schema-bound WarehouseAPI/MovementAPI participants, their exact Decimal helpers,
reference checks, embedded inputs, and ordered timestamp writes. Add an application
routine, not a generic mutation hook: it checks the lot/product and existing balance
identity, computes an exact positive receipt, then writes stock and history under one
restartable outer transaction. Native direct reads are explicit schema-coupled reads;
generated participants validate the managed record before writes. All derived locals
are recreated per attempt. Invalid input, duplicate ledger IDs, and native errors roll
back stock, totals, indexes and references. Keep identifiers/timestamps caller-stable.
Test exact Python equivalence, lookup through the ordered index, duplicate/validation
rollback, forced restart, and a coordinated competing receipt. No storage change.

### Reuse the inventory transaction workflow, issue 48 prerequisite

Inventory already owns quantity/time validation, atomic receive/move/adjust, ledger
construction and history queries. Give its existing balance lookup a private _change
method seam, matching _apply_balances, so another aggregate boundary can reuse those
business transactions without duplicating them. Preserve every current behavior and
model binding in this refactor. Existing inventory workflow tests cover it; C901 ten.

### Move the inventory aggregate boundary to Bin, issue 48

Reuse Balance, catalog/lot identities, Movement v4, Inventory's transaction seam,
patch_many, equality-index pagination and schema-bound M participants. Add independently
addressable StockLocation and StockBin roots; Bin owns balances and its local stored
sum. Location references Warehouse and Bin references both, enforcing lifetimes with
existing reverse indexes. Length-prefixed coordinate identities avoid hash collisions;
normal encoded-key limits still apply. Warehouse v3 remains a historical compatible
shell; no metadata conversion or automatic adoption occurs. New BinInventory is the
recommended application API; old Inventory and historical benchmarks remain explicit
legacy layout examples. Read a bounded bin aggregate to validate its coordinates;
that cost may grow with balances in that bin, never unrelated bins.

Keep reporting bounded by bin pages and compute warehouse totals by explicit traversal,
not a transactional cross-root total. Retain the 1,000-balances-per-bin and
10,000-nodes- per-bin limits, removing the warehouse-wide cap. Compare Bin ownership
with a Balance root in the native benchmark before claiming performance. Document the
choice in ADR17.

Use a maintenance-window app data move, not a new migration framework: require an
explicit writers-stopped attestation and initially empty target globals, then copy and
clear one bounded old warehouse and advance a managed checkpoint in the same atomic
callback. Crashes/failures cannot expose a half-moved warehouse; retry resumes from
committed progress. Keep old schema bindings/data representation and Movement
identities. The new service refuses writes during the move. Old Python/M writers must be
drained; this is deliberately not an online migration. Test rollback after copying and
clearing, resume and repeated completion, competing workers, reference lifetime
and >old-budget stock. C901 remains ten. No process-local data caches or framework
layout changes.

### Measure native inventory aggregate boundaries, issue 48

Reuse the disposable native harness, CountedStore, environment/latency reporting,
explicit source export, and reference/index audits. Compare warehouse v3, StockBin
ownership, and a script-only Balance-root alternative at 2 and 1,000 bins. Time an
insert/delete cycle with product/lot references, after three untimed warmup cycles;
exclude seeding, process startup and compilation. Emit all samples and node operations
for Python and the same cycle from persistent plain M. Keep audit traversal paginated.
The alternatives have different local total/reference work; report that limitation.
No comparison with relational systems or claims from memory timings. Existing stock
workflows and historical benchmark baselines remain intact. C901 remains ten.

### Keep M receipts usable after the bin data move, issues 48 and 54

Reuse the existing receipt transaction, Decimal arithmetic and Movement inputs. Add a
receivebin entry that selects StockBin addressing/participants while receive retains
its historical binding. Validate coordinate roots and the managed stock-move checkpoint
before stock writes. Keep the same explicit 64-byte identifier domain and exact node
checks. Export BinAPI and StockMoveAPI alongside the existing schema-bound sources;
these are application bindings, not another framework persistence API. Parameterize
the native equivalence/rollback/restart/competition tests over both layouts. Neither
entry point silently migrates data. C901 remains ten; no new generic abstractions.

### Qualify native stock fixtures and avoid repeated coordinate reads

CI exposed that the process worker test also ran against a memory-only fixture, and
that seeding all 1,600 lots/balances in one TP exceeded native buffers. Restrict the
worker test to its actual NativeStore and seed positions incrementally without reducing
the dataset or assertions. Separately, a failing counted regression shows _page reading
the same location once per returned balance. Resolve the coordinate once per loaded bin
and reuse it for rows, validating even empty bins. No contract change; C901 ten.

## Independent pre-release qualification

Baseline cf3d826fe8fec5f6810943c072a5cdead18af713, package 0.5.0. The checkout
contains only the pre-existing untracked .serena directory. No tags or GitHub
releases exist. Current Checks CI passed for that baseline. Reuse its Python 3.12,
Pydantic 2.12.0/2.14.3 and r2.02/Ubuntu 22.04, r2.06/Ubuntu 24.04 matrix, existing
canonical/legacy fixtures, migration fault tests, coordinated workers and native M
physical-equivalence tests. Ruff C901 currently passes at ten.

Confirmed gaps: qualification and compatibility policy are scattered; every PR also
runs extended benchmarks; installed-wheel smoke uses MemoryStore only; no isolated
sdist/native consumer or release-artifact manifest exists; getting started installs
unversioned main. Existing inventory migration's writers_stopped boolean is an
operator assertion and must not be presented as fencing external writers.

1. Qualification policy and compatibility contract. Document blocking PR/candidate
   gates and informational benchmark evidence, including permanent Python/M
   conformance obligations. Keep all routine native tests; move extended profiles
   and timings out of PRs. Expose the existing matrix as a reusable workflow.
2. Artifact qualification. Reuse hatchling, uv, smoke-wheel and native setup. Audit
   metadata/templates/licenses and version equality; build source/wheel, install
   each outside the checkout, reject source/editable imports and hash artifacts.
   Add a manual nonpublishing candidate workflow using the same mandatory checks.
3. Independent adoption fixture. Workspace permissions permit a separately packaged
   consumer under consumers, copied/built/installed in a temporary directory. Pin
   the exact candidate artifact, use public APIs only, and exercise real stock,
   history, migrations, Python/M and a historical source-built artifact upgrade.
   There are no published historical versions, so label that limitation explicitly.
4. Operations and cutover. Consolidate native installation, backup/recovery,
   process/retry and measured scale guidance. Document operator assertions, scoped
   framework barriers and unfenced raw/old writers. Add safeguards only where an
   executable downstream case demonstrates a gap. Run all available gates and
   native CI; record unavailable local runtime qualification separately.

The artifact installer and downstream app are clients of the existing public
interface, not new persistence adapters. Keep storage/schema/job formats unchanged.
No engine installation is implied by pip, and no version/tag/publication action
is authorized by candidate preparation. Preserve GPL-3.0-or-later; review exact
runtime dependency notices. The dependency order is policy, artifacts, consumer,
operations/evidence. Each phase is a separate verified commit. Required gates block
candidate qualification; stress and benchmark timing provide additional evidence,
not latency thresholds. No unsupported runtime is newly promised. No dependency
or public framework API expansion is planned. Keep complexity at ten.

Artifact audit confirmed a defect: the default sdist contained the untracked .serena
project files and binary caches. A source include list now excludes that state. The
new audit failed on the old artifact and passed on rebuilt source/wheel files. It
also checks exact M templates, GPL license, typed namespace, metadata/dependencies,
version equality and unsafe archive paths. A clean-tree flag distinguishes local
dirty experiments from committed candidate evidence. Four negative regression tests
cover local state, traversal and missing generated M code. Both fresh isolated
wheel and sdist installations executed the existing public migration/rebuild/mutation
smoke successfully, with Pydantic 2.14.3, outside the checkout. No runtime dependency
or storage change was required. Native isolated runs are wired into the disposable
CI database, using the same uploaded artifacts. Local engine files remain user-owned
755 rather than a privileged gtmsecshr installation; native qualification is delegated
to the existing two-engine CI, not claimed from those unpacked binaries.

The independent consumer is a separately packaged mccann_hub.inventory_ledger project,
pinned to the selected YHMF version and installed outside the checkout. It exercises
warehouse/location creation, receipts, transfers, adjustments, product/location/lot
queries, rollback, history and reference restrictions. A public-import regression
prevents reliance on private modules. Strict mypy now covers its source as well.
Fresh and source-built 0.4.0-to-candidate offline exercises passed; dependency checks
run before and after installation, raw metadata stays unchanged on reopen, and an
explicit additive Location migration resumes through new Database instances. Native
fresh/upgrade runs get separate databases through the existing harness, avoiding
newer framework checkpoints contaminating the older client's environment. Generated M
is exported/executed only after installing the current package. The 0.4.0 milestone
is pinned by full commit identity and is not represented as a published release.

Observed ergonomics: tuple-owned reads require a cast on the legacy public interface;
current typed selectors are read-only, so the consumer keeps the common tuple path.
Queries return root aggregates and must document their page units. No framework API
expansion was needed. Keep native/upgrade findings distinct from offline node fixtures.

### Fence cooperating legacy inventory entry points during cutover

A failing regression confirms that current legacy Inventory.receive still commits after
begin_bin_move despite the new service's checkpoint guard. Reuse the existing StockMove
checkpoint and transaction seam: any checkpoint presence disables current legacy Python
stock mutations, including after completion, without requiring old registries to register
a new model. The current application M receive entry likewise refuses checkpoint
presence; receivebin keeps its existing trusted-complete check. This is application
policy, not a new framework barrier or distributed lock. Old binaries, raw writers and
generic generated routines remain unfenced and must be drained by the operator. Record
that limitation and backup/validation/recovery steps in the runbook. Storage and metadata
formats remain unchanged. C901 remains ten; native tests check refusal with no writes.

Final review found a hard-coded 0.5.0 pin in the historical consumer builder. A minimal
0.6.0rc1 fixture failed while the 0.5.0 case passed. The builder now derives its
current pin and application version from the owned consumer manifest, then explicitly
binds the historical artifact to 0.4.0. Both regression cases and a complete isolated
0.4.0-to-current exercise pass. This makes future candidate selection repeatable
without silently retaining a stale requirement; it adds no framework API or dependency.

Final selected source 2579e6a passed manual candidate run 42532447268, including
both engines and journal modes, installed source/wheel smoke, real independent fresh
and 0.4.0 upgrade databases, and generated M. Each full native configuration passed
2,766 tests with 456 intentional skips; native stress passed 25 with four skips;
offline passed 1,269 and each Pydantic boundary suite passed 839. The final evidence
job promoted the exact clean-source manifest to passed. Downloaded artifacts in
ignored dist/qualified-2579 passed hashes/content checks, repeated local isolated
installs, consumer exercises and standard pip. Local repeated builds with a fixed
epoch were byte-identical within that environment. Lower-bound Pydantic 2.12.0
consumer fresh/upgrade cases passed separately. No local privileged engine, restore,
power-loss or replication qualification is claimed. The report-only commit records
these results; it does not replace or relabel the qualified source/artifacts. No
release, tag, public consumer repository or PyPI publication was created.

## TestPyPI Trusted Publishing

Reuse the existing candidate workflow, immutable qualified-candidate artifact and
artifact audit module. Add workflow_call to candidate qualification, then add the
user-specified publish-test.yml with workflow_dispatch only and environment testpypi.
A manual publish boolean defaults false so the whole chain can be validated without
an upload. Actual uploads require main. Keep id-token: write confined to the final
TestPyPI upload job; qualification and staging remain read-only. Publish the same
verified wheel/source bytes, never rebuilt files or evidence sidecars. Use the pinned
official PyPA action and a fixed TestPyPI endpoint, with no password/token secrets.

Extend check_artifacts with read-only qualified-manifest verification and staging,
reusing existing identity/hash conventions. Reject pending/foreign-run manifests,
wrong versions/commits, altered files, stale output directories and unexpected files.
Existing metadata audit writes pending evidence and must not be used to rewrite
passed evidence during publishing. Regression tests cover those failure boundaries.
Ruff C901 passes at ten before this change; retain the threshold. No version, storage,
runtime API, production publisher, tag or GitHub release change is planned. A future
publish.yml can be tag-triggered with a tag/version check and the same qualification.
GitHub currently has no testpypi environment; the workflow targets that exact name,
without changing repository environment protection settings in this task.

### Native setup failure handling

Both attempts of publishing run 43041742087 failed while APT resolved Ubuntu's
mirror hosts, before native tests or publication. Reuse the shared native setup
step in checks.yml so ordinary checks and candidate qualification use one policy.
Add explicit APT retries and transport timeouts, strict update errors and a
ten-minute setup deadline. A failure-only diagnostic step checks the four public
download hostnames with bounded DNS lookups. It preserves the original failure.
The public workflow interface, qualification gates and publisher remain unchanged.

Runner images already provide Ubuntu fallback mirrors. Keep that configuration
rather than add mirror rewriting or custom DNS servers without evidence that those
changes address the failure. Bounded retries help transient failures; persistent
runner DNS problems still prevent qualification and upload. Verify strict APT
failure handling against a deliberately unresolvable temporary repository, then
validate workflow syntax and rerun the full publishing workflow. Ruff C901 passes
at the existing threshold of ten before the change; no Python functions change.
No unrelated cleanup is included in the workflow file.

### Ubuntu mirror diversity for native qualification

Run 43045732482 confirmed that both runner images could resolve github.com but
failed or timed out resolving all three existing Ubuntu mirror hostnames. The
fallbacks share the ubuntu.com domain. Reuse APT's existing mirror-list interface
and add https://mirrors.kernel.org/ubuntu/ with priority zero on the disposable
runners, ahead of the original entries. Ubuntu lists this mirror on its package
download pages. Both supported release suites and their security pockets serve
signed InRelease metadata there. Keep the original fallback entries and APT's
signature and freshness checks. No custom DNS servers, pinned IP addresses or
package-verification exceptions are needed.

First test a private APT configuration with an unavailable primary mirror, then
the additional preferred mirror, against the system Ubuntu archive keyring. The
live native matrix must still pass before qualification and publication. The only
implementation change is runner-local mirror selection in the existing setup
step. There is no public interface, runtime dependency or storage change. Changing
the runner resolver is broader than this demonstrated package-download problem;
omitting documented YottaDB prerequisites would change the qualified environment.
Ruff C901 remains at ten; no Python functions change and no cleanup is included.
The private APT test returned 100 for the unavailable original mirror and zero
after adding the preferred mirror for both jammy and noble. Each successful update
verified signed metadata with the system Ubuntu archive keyring and the expected
suite codename. This local result does not qualify hosted-runner DNS or native tests.

### Production PyPI workflow

Reuse release-candidate.yml for full qualification, check_artifacts.package_version
for static Python version extraction, and the qualified staging verifier for exact
run/commit/version/hash checks. Add publish.yml with v* tag pushes and a manual
qualification-only dispatch. Only a tag push can reach its upload job. Keep the
configured publisher in this top-level workflow, with the pypi environment and
OIDC permission confined to that job. Local staging steps remain visible beside
each publisher; the shared verifier owns the integrity policy.

A small check_release script checks the two committed version declarations. Tag
pushes must exactly match v<version>, have an existing ## <version> changelog
section, and point to a commit reachable from fetched origin/main. Manual dispatch
checks versions and exercises qualification/staging without uploading, including
when dispatched on a tag. Test the release guard against temporary Git repositories
and malformed/mismatched identities. A manual hosted run validates the pipeline
without creating a release tag or uploading to production PyPI.

No package version, storage contract or application interface changes. Production
Trusted Publisher registration and GitHub environment protections remain maintainer
configuration, separate from the tested TestPyPI publisher. Actual tags, production
uploads and GitHub releases still require explicit authorization. Ruff C901 passes
at ten before implementation. Existing release docs contain deferred-publisher and
unpublished-version wording; update those statements as part of this change.
