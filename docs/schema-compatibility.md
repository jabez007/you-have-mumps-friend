# Schema compatibility

New installations use schema representation format 2. Storage remains layout 1
and uses codec version 1. The application revision is the owning model's positive
`__schema_version__`; it is independent of all three framework versions.

## What defines the contract

The registry produces sorted, compact ASCII JSON directly from typed declarations.
The existing properties/hierarchy/reference grammar supports the existing migration
inspector. Its codec/default markers and exact bound strings are YHMF-specific. Global names and owned field paths identify models. Field names identify
stored values. Scalar codec names distinguish strings, integers, booleans, floats,
Decimals, and bytes; key codecs also appear in reference declarations.

Required and nullable values, literal defaults, built-in numeric/string constraints,
secondary/unique indexes, ownership keys, owned revisions, and nondefault string
validation policies participate. Decimal constraints use exact coefficient/exponent
strings without rounding or exponent expansion. Decimal defaults preserve scale;
binary defaults use a `$bytes` hexadecimal object. Integer-keyed literal mappings
use a typed `$map` sequence. Default factories are marked but never executed.

Python class names, declaration order, titles, descriptions, source paths, custom
JSON Schema hooks, and serialization hooks do not define persisted identity. YHMF
uses its own codecs. Unsupported validation metadata fails registration rather
than silently losing a constraint. Time-only values, compound keys, recursive ownership,
and arbitrary unions remain outside the supported type set.

Date and datetime fields use explicit date-v1 and datetime-utc-v1 codecs. UTC ISO
values define their defaults and temporal bounds; no Pydantic JSON Schema output
participates. Existing scalar declarations retain their exact bytes. A string-to-date
or string-to-datetime conversion is a real type change requiring a named transform.

UUID fields use uuid-v1 with canonical lowercase strings for defaults. Unknown UUID
codec markers fail comparison safely. UUID field support does not extend primary
keys or change the identities of earlier primitive/temporal fields.

Virtual properties and Pydantic computed fields are outside the stored contract.
They add no nodes, indexes, defaults, or schema changes. A computed field may not
declare mapper persistence metadata. Changing a virtual display calculation alone
needs no data migration; using it to change persisted values in a validator still
follows the application revision policy below.

Mapper flags are compiled from registry semantics, rather than copied from raw
Field metadata. Adding `Field()` only for presentation, spelling a default `restrict`
policy explicitly, or supplying false index flags does not change the contract.

## Custom behavior needs an application revision

Changing a validator's body, closure, dependencies, or external configuration cannot
be reliably detected from Python metadata. The same applies to default factories.
Applications must bump the owning model's `__schema_version__` when such behavior
changes persistence semantics. For owned models, bump that owned model's revision;
its revision participates in the root hierarchy contract. Presence of custom
validation visible in Pydantic field/decorator metadata is recorded, but callable
implementations and arbitrary Python model hooks are never hashed. Changes to
those hooks require an application revision too.
Field reordering that changes a validator or factory's behavior also requires an
application revision; ordering alone cannot reveal those custom dependencies.

The ordinary migration executor validates every existing record under both bindings
and rejects transformations of existing fields. A revision does not grant permission
to overwrite data or make an unsupported migration executable. Validation-only migrations
validate, verify, retag, and activate without changing field or index values. Target
validation failure during the initial pass rolls back before a separate transaction
restores source access at the unchanged checkpoint. A concurrent checkpoint change
or interrupted restoration leaves maintenance held for inspection and recovery. Later
storage or execution failures remain resumable under maintenance. See the
[migration guide](guides/migrations.md).

An explicit named [Transform](guides/record-transforms.md) can authorize record
rewrites through the existing maintenance lifecycle. Its operation fingerprint and
versioned record digests pin the approved revision and outputs. Legacy snapshots
can be sources; activation publishes the reviewed target. Opening a database still
performs no conversion. Root identity/layout changes and revision downgrades remain
blocked, and older executors refuse the custom-operation marker.

## Existing databases

Format-one snapshots from v0.3 and v0.4 remain readable. Every transaction reads
both installed metadata nodes and verifies their agreement. Compatibility compares
the installed legacy declaration to a legacy declaration of the current model,
resolving local definitions and ignoring presentation metadata. Only the exact,
qualified incidental Decimal string pattern is normalized; arbitrary string patterns
and recorded validation constraints remain significant. Unrecognized differences
require review and fail ordinary access.

The legacy format did not record every constraint, config option, or custom
validator. Compatibility cannot reconstruct missing history. Keep application
revisions accurate and review dependency upgrades for validator behavior changes.
The committed 2.12.0/2.14.3 fixtures establish this specific cross-runtime behavior,
not equivalence of every supported Pydantic release.

Compatible access binds record metadata to the **installed** snapshot and
fingerprint. Reads, patches, aggregate replacements, and index rebuilds preserve
those bytes. Opening a database never converts schema metadata or retags records.
A format-only difference produces a no-op application migration plan. Consequently
older clients can continue using their original identities, subject to their own
legacy runtime checks.

New format-two databases require v0.5 clients. Older clients reject those identities
without modifying data. A genuine additive/index migration can activate a format-two target using the
existing bounded maintenance executor. Old jobs resume their exact saved source
and target snapshots, including a legacy target. Early abort, gate ownership,
checkpoint integrity, and atomic activation retain their existing behavior.

Pure parse/compatibility caches are bounded to 128 entries and keyed by complete
immutable inputs. Installed state is never cached: changing, removing, or corrupting
either live node is visible on the next operation. New canonical identities use an
exact byte comparison without parsing or hashing their snapshots on each CRUD call.
Unknown representation/codec versions and damaged metadata fail safely.

## Verification

`tests/test_canonical_schema.py` checks golden contracts under both CI Pydantic
versions. Legacy goldens remain committed unchanged. `tests/test_schema_contracts.py`
exercises CRUD, nested mutations, index rebuilding, old job recovery, binary
backfills, and metadata corruption on both adapters. Native CI separately exercises
concurrent initialization, metadata changes, rollback, and transaction restarts.

## Model configuration and declared data

Registration rejects `extra="allow"` on root and owned models. The mapper stores
only declared fields, so accepting extra instance data would silently discard it.
Declare persistent fields explicitly or use Pydantic's `extra="forbid"` or
`extra="ignore"` input policy. Values already discarded by older writes cannot
be recovered from the database.

The following overrides do not change the canonical contract:

| Setting | Persistence safeguard |
| --- | --- |
| `revalidate_instances` | Writes rebuild plain input mappings recursively before model validation. |
| `validate_default=False` | Writes validate each extracted field value explicitly. |
| `validate_assignment=False` | Writes revalidate the complete value rather than trusting assignments. |
| `allow_inf_nan=True` | Scalar codecs reject nonfinite values before persistence. |
| `extra="ignore"` | Pydantic discards undeclared input before constructing the value object. |

Existing format-two databases with the same declared fields retain their snapshot
and record identities when these safe options change. This patch adds no metadata
format conversion. Supported legacy policies retain the existing compatibility
checks; an unsupported legacy policy must be reviewed explicitly rather than
silently reinterpreted.
