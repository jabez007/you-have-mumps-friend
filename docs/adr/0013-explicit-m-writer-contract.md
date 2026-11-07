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

The outermost caller transaction must permit restarts, for example TSTART (), a
saved-variable list, or TSTART *. SERIAL is optional. Nested TSTART arguments do not
change a nonrestartable outer transaction. Bare TSTART remains unsupported: even
invalid-input failures request stable revalidation and can raise TRESTNOT/TRESTLOC.
The engine can restore staged data while leaving a positive transaction level at
the caller's handler. That handler must TROLLBACK an active TP and end the workflow.
Native tests assert prior participant data/schema/index restoration and caller
cleanup. Preserve stable revalidation; no unqualified restartability detector or
special-case stale-status return is introduced.

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

## Keyed ownership and stored sums, issue 38

Export keyed trees using the same field constants, scalar codecs, schema bindings,
TP policy, index transitions, and reference maintenance. insertchild/patchchild/
deletechild address leaf children beneath an already managed root, with matching
tx participants. Whole hierarchical labels return operation; Python creates and
replaces aggregates. Embedded/nullable containers and custom validators remain
rejected, including SupplierProduct. This is an explicit supported subset rather
than an incomplete aggregate serializer.

Each retry resolves root metadata, ancestry, counts, and identities anew. Changed
fields contribute ordinary dotted-name indexes and concrete reverse locations.
Scalar patches avoid unrelated reference target/count reads. Capacity checks reuse
the fixed-width c:N approach from record_budget, traversing only bounded structural
branches. Child markers/counts and affected ancestor sums commit with the leaf.

Concrete locations retain SHA-256 of canonical JSON. A bounded implementation of
[FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/final) uses YottaDB byte/bit
intrinsics and exact 32-bit words, without a new plugin or native extension. It is
an internal identity implementation, not a cryptographic certification claim.
Known vectors, padding boundaries, binary/UTF-8 bytes, and seeded cases are compared
with hashlib on native. Existing flat digests remain unchanged.

Sum deltas retain decimal-sum-v1's 18-significant-digit source and 40-unit-digit
aggregate domains. Signed digit arithmetic and canonical Decimal text match Python
without native precision loss. Target constraints and normal indexes are reused.
Raw M precision limits also apply to generated sum text, preserving the qualified
subset. Per-call immediate constraints remain explicit; caller composition does
not provide Python patch_many's net-zero coalescing or deferred group claims.

New managed child operations require existing metadata and never adopt raw data.
No schema/layout bytes change. Existing flat APIs retain their behavior; older
source must be regenerated for these capabilities and rejects changed bindings.

## Stored-value readability, issue 49

Keep incoming M inputs on their qualified conservative domain. Existing stored
values have a separate classification. Recognized values outside M's capabilities
return unsupported without writes. This is a refusal to validate or manipulate the
value, not a claim of Python validity. Definite malformed tags, syntax, object shape,
identity, or enforceable constraint violations remain corrupt. Reuse the existing
UTF-8, integer, and Decimal parsers rather than add another codec.

For stored Decimals inside the exact unit domain, apply precision constraints to
the coefficient with trailing zeros removed and normalize signed/scaled zero for
validation only. Preserve the original payload for unaffected fields and ordinary
index normalization. Do not depend on ambient Decimal context or rewrite databases
to make valid existing data fit the input subset. Schema/layout bytes and Python
validation are unchanged; older exported source needs regeneration.

## Bounded location memoization, issue 47

Cache only SHA-256 of the exact canonical location bytes. The reserved process-local
%YHMFDigestCache contains an algorithm marker and at most 128 entries, each with
at most 4,096 input bytes. Clear entries at capacity. Normal M local scoping and TP
restoration apply; entries are pure and remain correct even when retained through
a restart. No database invalidation protocol is needed. Never put schema, records,
index claims, references, counts, or transaction decisions in this cache.

The public writer still validates all live state on every attempt. Stored digests
remain byte-identical and source regeneration adds no schema/layout conversion.
The native benchmark compares otherwise identical generated source with and without
memoization, including cold misses and a working set above the entry cap. Separate
products versus one shared product expose reverse-count contention alongside native
block conflicts. Raw samples, hash calls, and TP attempts separate latency gains
from retry counts. No native hashing dependency or new persisted hash format is added.


## Qualified scalar extensions, issue 55

Extend the shared scalar compiler and validation seam, preserving schema and codec
bytes. Finite str/int domains use exact primitive membership, bounded to 256 members
and 32,764 bytes of escaped scalar JSON. Calendar dates and UTC microsecond instants
use temporal-v1 tags and exact Gregorian/lexical validation. Python validators and
unsupported constraints remain rejected; no application code runs in M.

Nullable qualified scalars use input(field,"%null")=1 with no scalar/other children.
Internally distinguish null from a tagged non-null payload so strings never become
sentinels. Persist the existing n: node; keep its equality index and omit its unique
claim, as Python does. Missing complete-record input is still invalid. References,
containers, lists, boolean choices, and other unqualified families remain rejected.
Qualification includes native differential, failure, rollback, and restart tests.
Regenerate source for these capabilities without database conversion.


## Root and non-leaf scalar patches, issue 53

Reuse the existing hierarchical mutation path for explicit patch/patchtx root
interfaces, including flat models. Empty internal ownership paths identify roots;
non-leaf patchchild uses normal concrete paths. Normalize flat configuration into
the same owner seam and retain root metadata semantics. old() accepts declared
collection markers with consistent immediate count presence, without scanning
unrelated children. Keys, derived fields, collections, and unsupported validation
remain rejected. Changed indexes/references use the existing transitions, with native
rollback/restart qualification. Whole aggregate replacement and tree construction/
deletion retain their separate contracts. No storage or metadata conversion occurs.


## UTF-8 strings, issue 55

Use one byte-oriented codepoint decoder for existing UTF-8 validation and canonical
JSON quoting. Count codepoints for string constraints; retain UTF-8 record/key bytes.
Emit Python-compatible lowercase JSON escapes, including surrogate pairs, independent
of $ZCHSET. ASCII identities remain byte-identical. Reuse the same string rules for
finite domains and reference keys. Peel stored/internal tags with byte operations
before rejecting invalid text, so UTF-8 mode cannot raise BADCHAR before validation.
Native tests run both M and UTF-8 modes; the harness resolves the installed ICU major
version for UTF-8 subprocesses without adding a dependency. Existing byte/key/location
budgets still apply. General regex translation remains rejected because Python regex
semantics cannot be represented exactly by M patterns. No storage conversion occurs.


## Empty non-leaf owners, issue 53

Permit empty non-leaf child insertion/deletion through the existing owned operation
path. Create declared c:0 collections, derive/validate zero totals, and maintain
ordinary indexes, references, and parent counts. Do not evaluate application defaults
or accept supplied collections/totals. Reject a positive collection minimum before
writes. Delete only owners whose declared immediate collections are empty, after
normal stored-shape validation; populated owners return operation. Keep capacity
traversal's leaf classification and existing node budget. This is a bounded subset,
not implicit recursive deletion or whole-aggregate replacement.


## Empty hierarchical roots, issue 53

Select the root owner constants through hconfig for ordinary insert/delete while
retaining the existing preflight and attempt transaction. First insert uses the same
unmanaged-global check and atomic metadata installation as flat roots. Derive zero
totals and empty child collections; never evaluate application defaults. Empty delete
keeps incoming-reference restrictions; populated delete and tree replace still return
operation. Native qualification covers concurrent first writes, unique contention,
raw different-key data, initial metadata rollback, and caller restarts. No schema or
record/index bytes change, and no second initialization/adoption path is introduced.


## Ordered-v1 transitions, issue 54

Dispatch index tokens to the existing ordered-v1 contract while retaining the same
transactional index/unique maintenance. Construct native numeric strings from exact
Decimal coefficient/power; reuse sourceunits to enforce the ordered numeric domain.
Use byte-oriented NUL/SOH escaping for strings, canonical D:/T: bytes for temporal
values, and z:null without a unique claim. Root/owned patches and stored sums retain
their existing transition paths. Native differential/restart tests qualify these
identities; raw records, schema metadata, and index namespaces remain unchanged.


## Non-null embedded owners, issue 54

Compile keyless embedded owners and retain the existing o:1 hierarchy. Complete
writes validate nested local arrays by trusted addresses, then recursively invoke
attempt at each embedded owner inside the same outer TP. Reuse hwhere, ordinary
index claims, references, and schema checks; no parallel serializer/index subsystem
or flattened persistent representation is added. Scalar patches validate immediate
embedded presence while leaving unrelated descendants untouched.

Qualify nonempty non-null embedded models without child collections inside them,
with at most 14 declared ownership segments and the existing 64-declaration/16-field
bounds. Include fixed embedded widths in record capacity. Reject embedded rollup
sources until their delta composition is qualified. Nullable embedded objects are
a separate step. Differential, malformed-data, rollback, restart, and near-budget
native tests qualify this subset. Schema/layout bytes remain unchanged.
