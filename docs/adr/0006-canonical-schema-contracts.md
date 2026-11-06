# ADR 0006: Canonical declarations with legacy storage identities

Status: implementation decision for v0.5.

## Existing mechanisms and problem

The registry compiles field and ownership metadata, then fingerprints Pydantic JSON
Schema. Committed 2.12.0 and 2.14.3 Decimal fixtures reproduce different identities
for the same declaration. Schema inspection validates snapshots, migration jobs
save exact manifests, and record metadata repeats the installed fingerprint.

## Decision

Generate a small framework-owned declaration from registry field specifications,
field constraints, literal defaults, and persistence-relevant model policy. Reuse
the existing declaration, hierarchy, and reference grammar so the established
comparison and migration modules remain the consumer. This replaces the proposed
separate field-array grammar in ADR 0002. It does not use Pydantic JSON Schema to
generate format-two fingerprints.

Format two declares its format, layout, and scalar codec versions separately from
application schema revisions. Globals and owned storage field names identify
models and fields. Deterministic properties, required fields, and relationships
exclude titles, docstrings, Python class names, source locations, and generated
Decimal lexical patterns. Literal defaults preserve Decimal and byte values.
Factories are described without execution. Unknown constraints fail registration.

Custom validator and factory function bodies are not reliably fingerprintable.
Applications must advance the root or owned `__schema_version__` when those
semantics change. Supported declarative constraints and validation configuration
participate directly. This policy does not claim to detect arbitrary Python code
changes automatically.

## Compatibility

New globals install format two. Existing globals retain their original snapshot
and fingerprint. A compatible legacy snapshot binds an immutable model
specification to that installed storage identity inside each transaction attempt.
New writes preserve the legacy identity. No record conversion or metadata write
occurs merely because v0.5 opens a database.

Legacy compatibility checks use the established declaration parser and a narrowly
normalized legacy candidate. They ignore presentation and the known generated
Decimal pattern difference, while preserving storage types, ownership, versions,
indexes, references, defaults, and recorded constraints. Legacy metadata cannot
prove semantics that older versions did not record. Explicit application schema
revisions remain necessary for those changes.

Pure snapshot parsing and normalization may use a bounded cache keyed by the exact
immutable snapshot bytes. Every operation still reads the live fingerprint and
snapshot and checks their agreement. A change to either node cannot reuse a
positive installed-schema decision. Cache entries never hold mutable documents.

Actual data migrations continue to use saved source and target manifests, bounded
record work, the maintenance gate, and atomic activation. Resume binds the model
to the exact saved format, including jobs created by v0.3/v0.4. This avoids a
second migration framework. Metadata-format differences alone do not require a
data migration, and there is no automatic in-place conversion of legacy metadata.

## Alternatives and tradeoffs

Changing only the hash input would strand legacy record fingerprints. Retagging
every record on framework upgrade would add unnecessary writes and recovery work.
A second canonical compiler and comparison engine would duplicate existing rules.
Retaining the proven grammar with a framework-owned producer keeps those rules in
one module. Legacy checks are conservative where recorded metadata is incomplete.

## Acceptance

Golden fixtures must agree under both qualified Pydantic versions. Legacy fixture
reads and writes must leave snapshots and stored identities intact. Real changes,
unknown formats, and damaged metadata must fail. Legacy migration and rebuild jobs
must resume and complete on both qualified native YottaDB versions before Phase 2.
