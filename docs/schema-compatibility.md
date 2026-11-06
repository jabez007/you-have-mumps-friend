# Schema compatibility

New installations use schema representation format 2. Storage remains layout 1
and uses codec version 1. The application revision is the owning model's positive
`__schema_version__`; it is independent of all three framework versions.

## What defines the contract

The registry produces sorted, compact ASCII JSON directly from typed declarations.
The existing properties/hierarchy/reference grammar supports the existing migration
inspector. Global names and owned field paths identify models. Field names identify
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
than silently losing a constraint. Dates, times, compound keys, recursive ownership,
and arbitrary unions remain outside the supported type set.

## Custom behavior needs an application revision

Changing a validator's body, closure, dependencies, or external configuration cannot
be reliably detected from Python metadata. The same applies to default factories.
Applications must bump the owning model's `__schema_version__` when such behavior
changes persistence semantics. For owned models, bump that owned model's revision;
its revision participates in the root hierarchy contract. Presence of custom
validation is recorded, but its callable implementation is never hashed.

The migration executor still validates every existing record under both bindings
and rejects transformations of existing fields. A revision does not grant permission
to overwrite data or make an unsupported migration executable. Pure validation-only
migrations remain outside the current additive/index executor's scope.

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

A genuine additive/index migration can activate a format-two target using the
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
