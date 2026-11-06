# ADR 0001: Transactional schema installation and integrity

Status: accepted for v0.3.1.

## Problem and existing capabilities

`Database._check_schema` checked a fingerprint without checking its snapshot.
Initial writes checked only the destination record for unmanaged data.
`schemas.inspect_schema`, `Store.children`, and `Store.atomic` already provide
snapshot inspection, bounded data-presence checks, and native restart handling.

## Decision

Keep schema verification in the `schemas` module. Each CRUD transaction attempt
reads both installed nodes. A matching fingerprint requires byte-for-byte equality
with the registered snapshot. This establishes the same identity as parsing and
hashing that known snapshot without doing either on every operation. A different
fingerprint uses full inspection before reporting a schema mismatch. Incomplete
or corrupted metadata raises `StorageError`.

Before automatic installation, check both the application global's root value
and its first descendant. The check and both metadata writes share the record
transaction. Concurrent initial writers conflict on the metadata nodes and retry
against the installed schema. Failed writes roll back installation as before.

There is no process-local cache. Every operation sees metadata inside its current
transaction, including modifications committed by another process. Matching
snapshot comparison still reads and compares up to one node of bytes; avoiding
that read would require stronger guarantees about every metadata writer.

## Alternatives and compatibility

Startup-only verification or a cached digest could conceal subsequent changes.
An extra integrity marker cannot detect arbitrary snapshot damage unless the
snapshot is read or all writers participate in a new protocol. Full parsing and
hashing every operation adds unnecessary work for known matching bytes.

The storage layout, snapshot bytes, fingerprints, migration barriers, and
existing migration jobs remain unchanged. Existing v0.3 databases with intact
metadata keep working. Data without schema metadata now requires explicit
adoption or external migration; inserting at another key cannot confer trust.

## Validation

Behavioral regressions cover root values, raw records at equal and different
keys, missing metadata, altered snapshots, and read/write/query/delete access.
The native process tests pause transactions after their initial schema read,
commit one writer, then release its stale competitor to exercise TP restarts.

## Explicit adoption

`Database.adopt(model)` is a standalone transaction for small globals already
encoded in layout one. It reuses `record_values`, Pydantic validation,
`record_nodes`, `indexes.replace`, and `references.replace`. It compares every
stored node against the re-encoded validated object, accepting absent or fully
matching record metadata. No unknown field bytes are removed or overwritten.
The whole operation, including self-reference validation, is atomic. It is
bounded to 100 records and 10,000 encoded record nodes, with an additional
per-record traversal budget. This avoids adding a second resumable migration
engine for importing arbitrary legacy formats. Existing migration execution
requires installed source schemas and cannot safely perform this adoption.
