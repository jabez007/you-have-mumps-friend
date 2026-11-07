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

Canonical generation and legacy integration are independently committed in
`28c5141` and `e24b26d`. [CI run 42288583847](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42288583847)
passed all five jobs: 883 tests per native r2.02/r2.06 environment, 464 local tests,
and 138 selected compatibility tests per Pydantic version. Both complete local
non-native suites also passed under 2.12.0 and 2.14.3. Ruff, formatting, complexity,
strict mypy, build, isolated wheel smoke, and the existing native benchmark ran.
The foreign-runtime compatibility regression fails against `268ab73` and passes
with the new binding. No dependency, record-layout, or application-data conversion
was introduced. Phase 1 is complete before Phase 2 work begins.

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

The codec fix (`c93778e`) and adversarial tests (`0b57ac0`) are independently
committed. [CI run 42290473930](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42290473930)
passed all six jobs: 916 tests in each native environment, 14 additional native
stress cases per environment, 483 local tests, 146 selected tests per Pydantic
version, and 19 cases in the separate memory adversarial stress job. The longer
local state-machine run passed in 18.24 seconds; the complete routine local suite
passed in 5.98 seconds. Ruff/complexity, format, strict mypy, packaging, isolated
wheel smoke, and native benchmarks passed. No generated mutation integrity defect
was found; the confirmed codec error boundary received minimal failing regressions.
Phase 2 is complete before application implementation begins.

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
passed all six jobs: 970 tests per native environment, 14 additional native stress
cases per engine, 169 compatibility cases per Pydantic version, and the 19-case
adversarial job. Both complete local Pydantic suites passed 514 tests. Ruff,
formatting, complexity maximum ten, strict mypy for the library and example,
packaging, and isolated v0.5.0 wheel smoke passed. Native tests verified double-spend
prevention, forced workflow restart, and ledger/index rollback; the native demo
and both benchmark runners completed on r2.02 and r2.06.

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
Both native engines executed the controlled caught-signal tests and four-process
counter regressions. Issues 1 and 7 are closed after that qualification. The
configuration matrix for issue 3 has five failing-before-fix declaration regressions;
all safe-option, rollback, and fingerprint checks pass under both Pydantic versions.

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
