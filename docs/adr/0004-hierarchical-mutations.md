# ADR 0004: Direct addressed mutations with explicit validation limits

Status: accepted for v0.4.

## Existing capabilities and interface

`Database._run` and `atomic` already provide retry-local handles and rollback.
Record codecs already know ownership, presence/count markers, and scalar encoding.
Index and reverse-reference modules already calculate identities from concrete
field paths and apply transactional differences. Reuse these implementations;
add addressed field entry builders rather than another indexing subsystem.

Address mutations by registered root model and key. An `at` tuple traverses
embedded field names or child collection name/key pairs. For example,
`at=("bins", "A", "lots", "L1")` identifies a lot owned by bin A. This retains
parent-scoped identity and prevents arbitrary global writes. `get` gains optional
`at`; root calls retain their existing return type. Owned reads return `Model`.
`patch(..., changes=..., at=...)` returns `None` and changes scalar or Reference
fields. `insert_child(..., collection=..., record=...)` and
`delete_child(..., at=...)` handle ownership changes. All compose inside `atomic`.

Alternatives considered: passing native `Path` would bypass declared ownership;
registering children independently would confuse scoped identity; implementing
patch as load/save would retain whole-tree costs. A persistent child handle would
add a second lifetime interface beside the existing transaction handle.

## Validation

Each transaction attempt checks installed metadata, root existence, and every
traversed ownership marker. Patching a leaf reads and validates that complete
leaf, retaining cross-field validators and model validators. If validation changes
another leaf field, its data and secondary entries are changed together. Keys
cannot change, including through validators.

For an owner with owned descendants, scalar patches are allowed only when the
owner has no custom validator, custom initialization, or post-init hook. Validate
selected fields using their annotated constraints and owner configuration. This
avoids loading unrelated collections. Ancestors on an owned traversal must meet
the same local-validation requirement. Core-schema function validators, including
Annotated validators, are detected. Reject unsupported dependency shapes with
`MutationError`; callers can use full `get`/`save` inside `atomic`.

Whole saves and child insertion normalize model instances recursively to plain
declared field data before Pydantic validation. This keeps unchecked copies from
bypassing constraints when a model sets `revalidate_instances="never"`. The shared
record helper handles owned subtrees and references without invoking serializers;
get and migration readers already reconstruct plain nested input from storage.

Collection insertion/deletion validates collection length constraints against its
new count. New child subtrees receive complete model validation. Deletion reads
the affected subtree to release its outgoing references and indexed occurrences.
References can only target independent roots, so owned children cannot have
independent inbound references. Ancestor validators are never silently bypassed.
The policy is conservative: some validators that are safe locally are rejected
because their dependencies cannot be established.

## Storage, consistency, and cost

Scalar patches preserve node count and write changed scalar nodes only. Index and
reference builders receive only changed occurrences, retaining their existing
root/key/full-owned-location identities. Unrelated secondary entries are untouched.
Embedded objects are patched by addressing them; patch does not replace owned
subtrees or synthesize absent embedded objects.

Child insertion/removal changes the child's subtree and its collection count.
Preserve layout-one limits of 1,000 children per collection and 10,000 record
value nodes. Insertion counts the root's existing value nodes with bounded
traversal because legacy records have no trustworthy cached size. This is an
explicit O(aggregate-size) invariant check, not a whole-model load or rewrite.
Deletion reduces size and needs no root scan. Adding a size cache now would
become stale when older clients save, so defer that optimization.

No revision metadata or storage format changes. Each mutation reads its affected
state inside TP. Independent fields or children preserve each other's changes;
competing writes to the same field serialize and the later committed assignment
wins. Arithmetic requires a read inside `atomic`. Unique-value conflicts allow
one claimant. Failed claims, storage failures, and restarts roll back data,
secondary entries, and collection counts together. Parent deletion conflicts with
nested mutation; the restarted operation checks root/owner existence again.

Direct external global writes remain outside the mapper's integrity contract.
Partial operations validate addressed markers and values, not unrelated records;
use full reads, inspection, and index audits to investigate suspected damage.

## Evidence required

Tests cover direct node operation counts as unrelated children grow, complete
leaf validation, rejected ancestor validators, ownership, collection limits,
indexes/references, caught failures, and migration barriers. Native coordinated
process tests cover independent/same-field changes, unique claims, parent/child
deletion, forced restarts, and atomic composition. Native benchmarks compare
direct node assignment, framework patch, and whole-aggregate replacement; no
in-memory latency is evidence of native performance.


The dependency-compatibility CI matrix runs fingerprint and mutation validation
regressions under Pydantic 2.12.0 and 2.14.3. This explicitly exercises the core
schema inspection policy at both qualified dependency versions.
