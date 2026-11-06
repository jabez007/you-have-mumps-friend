# Integrity hardening and hierarchical mutations

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
