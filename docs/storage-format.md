# Storage layout version 1

This reference defines the prototype's byte contract. The earlier architecture's
unprefixed string values and keys were illustrative. This contract distinguishes
types explicitly and never stores an entire record as an opaque blob.

Canonical ASCII JSON has no trailing newline or whitespace outside strings.
It uses the short JSON escapes for backspace, tab, newline, form feed, and carriage
return. Other control and non-ASCII characters use lowercase `\uXXXX` escapes;
supplementary characters use surrogate pairs. Quotation marks and backslashes
are escaped; forward slashes and HTML characters are not. Object keys in schema
snapshots are sorted. Arrays retain their declared order.
Nonfinite JSON numbers are rejected when generating or inspecting snapshots.

## Keys and paths

Integer keys use canonical decimal ASCII, with at most 18 digits excluding the
minus sign. Integer `7` is subscript `7`. String keys use `s:` followed by UTF-8,
so string `"7"` is subscript `"s:7"`. Empty string keys become `"s:"`.
Booleans, numeric aliases such as `01`, and out-of-range integers are rejected.
Numeric keys retain native numeric collation. String keys collate as bytes.

Global names match `^[A-Za-z%][A-Za-z0-9]{0,30}` after the leading literal caret.
Subscripts are nonempty bytes without NUL. Paths have at most 31 subscripts.
The conservative encoded-key bound is the global-name byte length plus 2,
plus twice each subscript's byte length plus 2. It must not exceed 1019.
This bound may reject paths the engine could accept. Region-specific limits
can be stricter; the engine's errors propagate.

## Scalar values

[ADR 0012](adr/0012-retain-tagged-record-values.md) retains tagged layout-one record
values after native M semantic probes. Ordered index encoding is a separate versioned
contract; it does not require replacing record scalars or adopting raw native writes.

| Type | Encoding | Example |
| --- | --- | --- |
| Null | `n:` | `n:` |
| Boolean | `b:0` or `b:1` | `b:0` |
| Integer | `i:` plus canonical decimal | `i:-42` |
| Float | `f:` plus Python's shortest round-trip representation | `f:1.5` |
| Decimal | `d:` plus decimal representation with exponent and trailing zeros | `d:12.50` |
| String | `s:` plus UTF-8 | `s:Main` |
| Bytes | `x:` plus standard padded Base64 | `x:AP8=` |
| Date | `D:` plus fixed-width Gregorian date | `D:2026-11-06` |
| Datetime | `T:` plus fixed-width UTC instant with six fractional digits | `T:2026-11-06T12:00:00.000000Z` |
| UUID | `u:` plus lowercase hyphenated 128-bit identifier | `u:12345678-1234-5678-9abc-def012345678` |

Nonfinite numbers and unsupported types are rejected. Node values have a
prototype limit of 32766 bytes, including the type tag. An absent node returns
`None` from the storage adapter; a persisted null is the distinct value `n:`.

Date values cover Python's calendar years 0001–9999. Datetimes require a timezone,
normalize to UTC, and reject overflow during normalization. The original zone and
fold presentation are not stored; the instant is preserved. Leap seconds and naive
datetimes are unsupported. Reencoding rejects compact dates, alternative offsets,
missing fractional digits, and other noncanonical stored forms.

Within each temporal type, encoded payloads sort chronologically. They are tagged
strings, not M numeric values; numeric coercion is not date arithmetic. Equality
indexes normalize equivalent timezone offsets to the same instant. This does not
add range-query APIs or change general index ordering.

New temporal fields declare date-v1 or datetime-utc-v1 codecs in schema format two.
Temporal bounds have temporal_minimum/maximum/exclusiveMinimum/exclusiveMaximum
rules with canonical ISO values. Defaults use the same ISO values and existing strict
JSON literal validation. Unknown temporal codecs fail comparison safely. Layout and
global codec version remain one; all earlier scalar tags and schema bytes remain
unchanged. Older clients reject these new declarations/tags rather than reinterpret
them. Golden fixtures temporal-v1.json and temporal-schema-v1.json fix the contracts.

UUID fields declare uuid-v1 and use standard UUID objects. Uppercase, compact, and
braced raw encodings are rejected by reencoding. The version/variant bits are preserved;
plain UUID fields do not restrict them. Keys remain str/int: UUID is supported as a
scalar and equality/unique value, not as a primary-key codec. Defaults are canonical
UUID strings in metadata. uuid-v1.json fixes encoded boundary values and the schema.
Decoders reject invalid and noncanonical encodings.

Enum and Literal fields store the existing s:/i:/b: primitive encodings. Their
format-two declarations carry enum-str-v1, enum-int-v1, enum-bool-v1 or the
corresponding literal-* codecs, plus a sorted, deduplicated enum value array.
Homogeneous str/int/bool domains are supported; Flag, mixed/nonprimitive values,
and Enum use_enum_values=True are rejected. Aliases, member names, enum class names,
and declaration order do not affect identity. Nullable unions use the existing n:.
Enum defaults are primitive values in metadata. Exact typed lookup restores members;
unknown values and wrong primitive tags fail without invoking Enum._missing_.
choices-v1.json fixes representative schema and storage bytes. Existing schemas,
primitive encodings, and layout/global codec versions remain unchanged.

Scalar lists use scalar-list-v1 in format-two metadata with an explicit item codec
and nullable item contract. The field holds l:N and item values live at numeric
subscripts 1 through N, each using its ordinary scalar tag. Order, duplicates, and
null items are preserved; n: is a null list, l:0 is empty, and a missing field is
corrupt. Lists have at most 1000 items and count toward the 10,000-node root budget.
Reads verify the marker, consecutive canonical ordinals, and absence of nested item
nodes. Only supported plain scalar and finite-choice items are accepted; nested
containers, models, references, and Annotated item declarations are rejected.
Whole-field constraints and custom validators still run through existing adapters.

Equality indexes compare the complete ordered list using a canonical JSON array
of normalized scalar encodings. Null lists, empty lists, different orderings, and
different duplicate counts are distinct. Individual items are not indexed; native
key-size limits can reject long indexed lists even when the record itself fits.
Patches compare field node maps, setting changed ordinals and deleting only removed
ones. Net growth across a patch group is checked once per root before any writes.
New list fields and item-type changes require an explicit named record transform;
there is no automatic list-default backfill in the initial implementation. Existing
list-field validation/index revisions use ordinary migration validation. The golden
scalar-list-v1.json fixture fixes schema and physical nodes without altering prior
contracts or layout/global codec versions.

The [golden scalar fixtures](../tests/fixtures/scalars.json) contain literal
encoded values for readers in other languages. Float rendering still requires
cross-language qualification before a Go writer can claim compatibility.

## Records and installed schemas

Each record stores its declared fields under their ASCII names. Defaults are
persisted, including null and empty strings. Missing declared fields are corrupt
data; reads do not reconstruct them from defaults. The primary key is stored as
a typed scalar as well as in the path, and reads verify that they agree.

The reserved `%m` subtree contains raw ASCII metadata values for `layout`,
`schema`, `type`, and `fingerprint`. For a top-level `^Product`, `type` is `Product`.
The schema fingerprint is the lowercase hexadecimal SHA-256 of the canonical
snapshot bytes. These metadata values do not use scalar type tags.

`^YHMFSchema("Product")` holds the installed fingerprint, with the snapshot at
its `"snapshot"` child. The snapshot is compact, sorted-key, ASCII JSON.
Legacy format one includes Pydantic's validation JSON Schema. New installations
use framework-owned format 2 with explicit `format_version: 2`, `codec_version: 1`,
`layout: 1`, and an independent application `version`. Its declaration grammar
retains properties, required fields, hierarchy, and references for the existing
migration inspector. Scalar codecs, deterministic defaults, built-in constraints,
and reference key codecs participate; titles, class names, and descriptions do not.
See [the compatibility contract](schema-compatibility.md).
Schema installation and the first write occur in the same transaction.

Nested model schema versions, relationship kinds, and child-key fields also
participate through the snapshot's `hierarchy` entry. Scalar-only snapshots
omit that entry.

`^YHMFSchema`, `^YHMFIndex`, `^YHMFUnique`, `^YHMFRef`, and `^YHMFMigration`
belong to the framework.
The engine reserves
`^%Y*`, which the path validator rejects. A registry assigns each application
global to exactly one top-level model. Save supplies a complete replacement value
and writes its physical node differences;
it does not merge raw nodes or silently overwrite unmanaged records.

CRUD reads both installed metadata nodes on every transaction attempt. Exact
canonical bytes take the direct path; supported legacy bytes undergo integrity
and compatibility checks before binding the operation to the stored identity.
Bounded caches contain only pure results keyed by immutable declarations or full
snapshot bytes. They never cache installed-state decisions or hide changed nodes.
Missing or corrupt snapshot bytes fail safely. Automatic installation checks that the entire
application global is empty in the same transaction as metadata installation.
A root value counts as data even when there are no descendants.

The inspection interface verifies the canonical bytes and their fingerprint
before exposing installed metadata. An absent fingerprint with a snapshot, or
an absent snapshot with a fingerprint, is incomplete metadata and causes an
error. Inspection never rewrites either node. Unknown positive layout versions
can be inspected; inspection alone does not claim compatibility with them.

The comparison interface resolves local JSON Schema definitions and compares
declared paths, scalar types, validation, defaults, ownership, references, and
index flags. Unknown constructs are reported for manual review. Presentation-only
changes are excluded from canonical format-two identities. Compatible legacy
identities remain unchanged, including their record metadata. Real migrations
continue to retag records through the existing checkpoints.

Read-only migration plans use review format version 1. They contain source and
target fingerprint summaries, sorted changes, and ordered proposed steps. They
contain no record values and omit literal defaults unless explicitly requested.
They neither introduce migration-state globals nor change the installed schema.
This format is a review report, not an executable migration program.

The migration maintenance gate is `^YHMFMigration("gate")`, containing a
32-character lowercase hexadecimal job identifier. Current mapper data
operations read this gate inside their transaction, validate the owning checkpoint
and barrier, and refuse access to that job's model. Unrelated models remain usable;
maintenance jobs themselves remain serialized. Older clients honoring the global
gate continue to block conservatively. Schema inspection and planning remain available. The migrating model's
installed fingerprint is temporarily `migration:` followed by the identifier;
its source snapshot stays installed for inspection. This barrier also makes
older clients reject that model through their existing fingerprint checks.
Raw YottaDB writers must be coordinated by the operator. No new metadata is needed
for scoped access; the existing checkpoint already identifies the affected global.

Committed migration checkpoints live at `^YHMFMigration("jobs",job_id)` as
canonical JSON with format version 1, source and target fingerprints, a phase,
four record counters, and a hexadecimal encoded-key cursor. Status diagnostics
omit the cursor unless explicitly requested. Each checkpoint belongs to the
same transaction as its batch's data changes.

Index rebuild jobs add an `operation_fingerprint` field to their format-one
checkpoint. It is the lowercase SHA-256 hexadecimal digest of the canonical
`"operation"` child, whose document has `format_version: 1`,
`kind: "index_rebuild"`, and a sorted `fields` array of distinct declared index
names. A missing operation and digest identify a legacy schema migration.
Schema migrations continue writing the original checkpoint shape. Executors
reject jobs of another kind, and selection bytes must match the stored digest.
Older readers reject rebuild checkpoints rather than treating them as ordinary
schema changes.

Each job has `"source_snapshot"` and `"target_snapshot"` children containing
the canonical schema bytes. The checkpoints progress through `validate`,
`backfill`, `verify`, `retag`, optionally `cleanup`, then `activate` and `complete`. Each bounded pass stores
its last encoded record key as hexadecimal and resets it at the next phase.
The counters are `validated`, `backfilled`, `verified`, and `retagged`.
Normal checkpoints omit `pruning`. Explicit retirement of a terminal job adds
`pruning: true` to its checkpoint, without changing application metadata. All job
children are removed in bounded leaf batches, followed by the checkpoint root.
Executors reject marked jobs even when their manifests have already been removed.
Older maintenance readers reject the optional field; existing untouched checkpoints
retain their original shape. No gate is claimed for terminal metadata retirement.
Activation requires equal counters and publishes the target schema, completes
the job, and removes the gate in one transaction. Before any update batch or cleanup deletion commits,
abort can restore the source fingerprint and persist the `aborted` phase.

The executor adds scalar nodes and changes equality entries or unique claims at
root fields and inside existing owned objects and children.
It can promote existing scalar equality indexes to unique indexes without
rewriting their equality entries. It rejects occupied backfill paths and nonempty new-index
namespaces. Existing fields and retained index identities must remain byte-for-byte
unchanged. A uniqueness downgrade removes claims without rewriting equality
entries; an index removal retires both equality and claim namespaces for that
declaration path. Existing expected index nodes, reverse edges,
and reference target metadata are validated before updates. Verification
checks expected entries by record; it does not perform a full reverse index
audit for unrelated orphan entries.

After parent metadata updates, retirements enter `cleanup`. Each call removes
at most its batch limit of leaves under the exact retired namespaces, including
orphan entries and namespace values. A node is deleted only after checking it
has no descendants. The absence of committed leaves is durable traversal
progress; cleanup does not store an index-value cursor. The job's
`"cleaned_nodes"` child contains a canonical, nonnegative ASCII integer counting
cleanup deletions. An absent child means zero. This count commits with the
deletions and checkpoint and prevents abort after cleanup starts, even when
record counters are zero. Record counters are unchanged during cleanup.
Activation rechecks that all retired namespaces are empty and returns to cleanup
if they reappear. Other index namespaces and application globals are untouched.
Older library versions that do not recognize this phase refuse to resume it.

An unchanged-schema rebuild uses identical source and target snapshots and
fingerprints. It progresses through `validate`, `clear_indexes`, `backfill`,
`verify`, `activate`, and `complete`. Validation reads stored fields and record
metadata, checks that revalidation preserves encoded field bytes, and reserves
selected non-null unique values. Existing equality entries and claims are not
required to be valid. Reference edges and target metadata use the existing
maintenance checks; reverse-reference counts are not rebuilt.

`clear_indexes` drains both `^YHMFIndex` and `^YHMFUnique` namespaces for each
selected dotted field name, including orphan nodes. It uses the same bounded
leaf deletion and `"cleaned_nodes"` counter as retirement cleanup. Backfill
constructs selected entries from records and reservations, then verification
checks the expected entries and claims. `validated`, `backfilled`, and `verified`
count parents and must agree before activation. `retagged` remains zero because
record metadata is unchanged. Activation restores the original installed
fingerprint, completes the job, and removes the gate atomically. The installed
snapshot bytes remain identical. Unselected namespaces are untouched.

Logical migration paths contain field names only. Index namespaces join those
names with dots, including the full path for nested additions and promotions.
Physical backfill paths also contain encoded child keys wherever ownership
crosses a child collection. Only existing owners are visited; null embedded
objects or collections remain null, and no new child identities are introduced.
Presence and count markers, existing scalar bytes, reference edges, and index
identities must remain unchanged. A parent and every affected descendant commit
with that batch's checkpoint. Progress cursors and counters still identify
top-level parents. Target trees must satisfy the existing record node budget
and child limits before any backfill is committed.

New uniqueness constraints use job-scoped validation reservations at
`^YHMFMigration("jobs",job_id,"unique",digest)`. The digest is the lowercase
hexadecimal SHA-256 of canonical JSON containing the application global without
its caret, declaration path, and normalized value token as an array of strings.
Declaration paths use their full dotted form at every depth. Each node contains
canonical JSON with `index` holding that same array and `owner`
holding the decoded unique-claim owner array. The complete identity participates
in comparisons, so a digest collision fails closed. Hashing bounds the scratch
key length independently of the indexed value's length.

Reservations commit with validation checkpoints and detect duplicates across
batches. Nulls create no reservations or unique claims. Backfill, verification,
and metadata updates require each reservation to match the expected value and
owner. Reservations never participate in ordinary uniqueness checks and remain
under the job after completion or abort. They contain normalized values and
owner identities; normal status diagnostics expose neither. Promoting an index
requires its new unique-claim namespace to be empty before maintenance begins.

## Owned trees

An embedded object's field node has value `o:1`, and its declared fields are
children of that node. A null embedded object has value `n:` and no descendants.
An empty embedded object remains present through its `o:1` marker.

A child collection's field node has value `c:` plus its canonical decimal entry
count. `c:0` represents an empty collection. Each child key follows the key
encoding above. A child record's root has value `o:1`; its fields follow under
that root. Reads check the collection count, presence markers, and each child's
stored key against its physical path. The count is a consistency marker, not
a concurrency lock.

Each collection permits at most 1000 children. A whole record permits at most
10000 nodes, including record metadata and presence markers. Writers construct
and validate the full node plan before modifying storage. Readers bound every
collection traversal and reject records that exceed the node budget.

Owned models cannot declare independent globals, and ownership declarations
cannot be recursive. Saving a parent replaces its entire subtree in one
transaction. [Direct owned mutations](#direct-owned-mutations) change part of it.

## Direct owned mutations

`get(..., at=...)`, `patch`, `insert_child`, and `delete_child` resolve declared
ownership from a registered root. Scalar patches preserve every unrelated node
and collection marker. Secondary entries retain their existing full location
identities; only changed occurrences are passed to the normal index/reference
replacement routines. Root metadata and schema fingerprints are unchanged.

Child insertion/deletion updates only its subtree, affected secondary entries,
and the existing collection count. Insertion enumerates declared ownership keys
to preserve the aggregate node budget because layout one has no size counter.
Scalar patch preserves node count; deletion reduces it. No storage upgrade is
required for v0.4. See [ADR 0004](adr/0004-hierarchical-mutations.md) for validation
restrictions and concurrent mutation semantics.

## References and reverse entries

A non-null reference is `r:` followed by the target's canonical encoded key.
The target model comes from the declaration. For `Reference[Product]`, key
`"P42"` persists as `r:s:P42`. Null references use `n:`. Both the target global
and its primary-key field participate in the source's canonical schema snapshot,
along with its declared deletion policy: restrict, set_null, or cascade. Restrict
remains the implicit default and retains existing snapshot bytes. set_null requires
a nullable reference. Policies change schema identity, without changing edge layout.

Reverse entries use this layout:

```text
^YHMFRef("Product","s:P42")="1"
^YHMFRef("Product","s:P42","Order",1,"<path-sha256>")="[\"lines\",\"s:L1\",\"product\"]"
```

The target root contains the number of incoming reference fields. Each leaf
identifies the source global, source root key, and relative owned-field path.
The path is a compact ASCII JSON array with `ensure_ascii` escaping. The digest
is lowercase hexadecimal SHA-256 of those exact JSON bytes. Child keys retain
their canonical key prefixes in the array. The hash keeps deep owned paths
from consuming additional index subscripts. Native key-size limits still apply
to the combined source and target identity.

The bounded `referrers` interface selects
`^YHMFRef(target_global,encoded_target_key,source_global)` directly, then pages
source root keys and digest leaves in native order. It decodes the stored location
to an owned path and verifies the exact source field. M readers can walk those
same source-key and digest subscripts; there is no additional inverse store.
For a supplier membership, the source field is stored under
`^InventorySupplier("s:S1","products","s:WIDGET","product")="r:s:WIDGET"`, and its
reverse location is `["products","s:WIDGET","product"]` beneath the Product target's
`"InventorySupplier"` prefix. Removing the owned link removes its reverse occurrence
and decrements the target count in the same transaction.

Every write atomically changes the source record, reverse entries, and incoming
counts. A deletion reads the count node directly, which participates in native
transaction conflict detection. It also checks whether reverse children exist.
Deletion discovers a bounded closure before applying writes. Incoming cascade edges
delete their nearest keyed source owner; set_null fields on surviving owners are
validated and cleared; restrict edges from surviving owners block the operation.
All removed edges and index claims transition as a union, so cycles and overlapping
ownership do not expose invalid intermediate states. A self-reference alone does not
block deleting its source. Final child counts and lengths are checked after parent
dominance, and all changes share the same transaction. Limits and validation rules
are documented in the [reference guide](guides/references.md#understand-deletion).

Unreferenced targets can retain a count of zero. Deleting the target removes its
reverse root. Missing or inconsistent entries cause errors when mapper operations
encounter them. Raw writes can bypass enforcement; this version has no automatic
repair or complete consistency scan.

## Secondary indexes and unique claims

Equality entries use this structure:

```text
^YHMFIndex(global,field_path,value_token,owner_key,location_sha256)=location_json
^YHMFUnique(global,field_path,value_token)=owner_json
```

`global` omits the leading caret. `field_path` joins declared field names with
periods, without child keys. Each entry's `owner_key` is the canonical top-level
record key. `location_json` is the compact ASCII JSON array of physical relative
subscripts, including canonical child keys. Its lowercase hexadecimal SHA-256
is `location_sha256`. `owner_json` is the compact ASCII JSON array containing
the encoded owner key and the decoded location array.

For equality-v1, `value_token` is `v:` followed by an ASCII JSON string containing the normalized
typed value. Ordinary scalar and reference encodings remain unchanged except
for numeric normalization. Decimal normalization strips trailing coefficient
zeros and increases the exponent accordingly, without decimal-context rounding.
Nonzero decimals use `d:` followed by the signed coefficient, `E`, and exponent.
Every decimal zero maps to `d:0`; both floating-point zeros map to `f:0.0`.
For example, `12.50` and `12.500` share token `v:"d:125E-1"`.
String comparison uses exact code points without Unicode or case folding.
The [index-value fixtures](../tests/fixtures/index-values.json) define literal
tokens for decimals, negative zero, Unicode, NUL, null, empty strings, and bytes.

Unique fields also have equality entries. Under equality-v1, null values use the token
`v:"n:"` and create no unique claim. Every non-null unique field occurrence owns
its claim exclusively, even when multiple occurrences are under the same parent.
Claims are scoped to a top-level global and declaration path.

Field(ordered=True) declares index_codec: ordered-v1 in its yhmf field metadata.
The same entry/claim shape uses native canonical numeric subscripts for int/Decimal
values with at most 18 significant digits and nonzero magnitude from 1E-18 to below
1E18. Numeric zero is 0, fractions below one omit the leading zero, and redundant
fractional zeros are stripped exactly, independent of Decimal context. Values outside
this initial index domain are rejected before writes without rounding or fallback.
Record scalar encodings and stored Decimal scale remain unchanged.

Ordered date/datetime value subscripts are their existing D:/T: ISO encodings. Ordered
string subscripts are s: plus UTF-8, replacing byte 01 with 01 02 and byte 00 with
01 01, in that order. This preserves lexical order, empty strings, and prefix identity
without NUL subscripts. Null uses z:null and sorts after every supported nonnull
value. str/int finite choices use their primitive value encoding. Float, bool, bytes,
UUID, reference, and list ordering are not supported in this codec. Qualification
uses standard native collation; the golden ordered-index-v1.json fixes schema and
value bytes. Existing indexes without a marker retain equality-v1 exactly.

An index-codec change on an already indexed field is index_codec_changed and needs
an explicit named transform initially. A new ordered index can use ordinary additive
migration. Rebuild reviews identify ordered codecs and preserve the installed format.
Unknown index codecs fail schema comparison. No record layout or global codec version
changes, and no index is converted during ordinary CRUD.

Record replacement validates existing entries, releases removed claims, and
claims new values in one native transaction. Uniqueness is immediate per mapper
write. Index-definition changes participate in schema fingerprints and require
explicit migration for installed schemas. The additive executor builds new
scalar indexes and promotes equality indexes to unique under maintenance;
ordinary CRUD does not rebuild
indexes automatically. Native key-size limits include the full indexed value token.

## Query execution

Equality queries read owner keys directly below an equality value root, so one
owning root appears once even if multiple child occurrences match. Cursors are
exclusive owner keys in native collation order. Direct-key queries access the
record path directly and do not accept cursors. Query and explicit scan pages
have a maximum of 1000 roots, and each page runs in one transaction.

Index audits inspect the expected entries and claims for a bounded page of
records. They report missing or inconsistent entries without including indexed
values. They do not perform an exhaustive reverse scan or automatic repair.
Query execution reports encountered orphan owners and stale value matches as
storage errors. A missing index never causes an implicit global scan.

## Storage adapter

`Store` supplies `get`, `set`, `delete_tree`, bounded `children`, and callback
transactions with `atomic`. `children` returns immediate subscript names in
native order, strictly after the optional cursor. The limit is 1 through 10000.

`NativeStore` calls YDBPython directly. `MemoryStore` supplies rollback for offline
tests and supports the contract's integer and string collation. It does not model
other native numeric forms, native transaction restarts, or interprocess access.


Native application validation errors can arise before TP commit-time read-set
validation. The native adapter requests restart for mapper and value-validation
errors while `$trestart < 3`, then exposes persistent errors from the stable
attempt. This changes no stored nodes or formats. Runtime aborts and native
signals pass through. See [ADR 0005](adr/0005-native-validation-restarts.md).

## Record-transform job metadata

Named record transforms retain layout one and use the existing maintenance job,
snapshot, checkpoint, operation-fingerprint, reservation, and pruning structures.
Their operation is a canonical JSON object containing format_version=1,
kind=record_transform, the application name, and digest_version=1. Older executors
refuse custom operation markers they cannot execute.

Each validated record pins canonical JSON at jobs/job-id/records/SHA256(encoded-key),
under ^YHMFMigration, containing key (hex), source (digest), and target (digest).
The full key prevents hash collisions from collapsing identities. Digests hash
sorted relative field-node paths and exact encoded values; record metadata is
checked separately against the pinned snapshots. For each node, append a one-byte
path-part count, then four-byte unsigned big-endian lengths and raw bytes for each
part, then the four-byte value length and raw value. SHA-256 produces lowercase
hex digests. This format is fixed by tests/fixtures/record-transform-v1.json.

Backfill must reproduce the validated source and target digests. Source record
metadata stays installed on rewritten records until verification and retagging;
the model remains gated throughout. Index clearing increments the existing cleanup
counter, making abort unsafe after its first committed deletion. Application
records are never staged as serialized blobs in job metadata. See the
[transform guide](guides/record-transforms.md) for recovery and compatibility.
