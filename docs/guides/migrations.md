# Change a schema

Your Python declarations are the intended schema, and the database records the installed
one. Every transaction checks both the installed fingerprint and the snapshot bytes. When
they differ from the declaration, regular access raises `SchemaMismatch` before any write,
until you run a migration. Python class names and presentation metadata do not change the
fingerprint.

The steps are:

1. [Inspect](#inspect-the-installed-schema) the installed and declared schemas.
2. [Review a plan](#review-a-plan) for the migration.
3. [Stop other writers](#stop-other-clients-first).
4. [Run the migration](#run-the-migration), and [resume or abort](#resume-or-abort) if needed.

Automatic backfills handle a small set of changes. Use a reviewed
[record transform](record-transforms.md) when existing values or ownership must change.

The examples share this setup:

```python
from decimal import Decimal

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry, compare_schemas


class CatalogProduct(Model):
    __global__ = "^CatalogProduct"
    id: str = Field(primary_key=True)
    sku: str | None = Field(default=None, unique=True)
    price: Decimal = Field(default=Decimal("0"), index=True)


db = Database(MemoryStore(), Registry(CatalogProduct))
db.insert(CatalogProduct(id="P1", sku="SKU1", price=Decimal("12.50")))


class CatalogV2(CatalogProduct):
    __schema_version__ = 2
    note: str | None = None


next_db = Database(db.store, Registry(CatalogV2))
```

## What can migrate today

| Change | Executable |
| --- | --- |
| Add a scalar field with a literal default | Yes |
| Add a scalar index | Yes |
| Promote an equality index to unique | Yes |
| Downgrade unique to equality, or remove an index | Yes |
| Reorder fields or change descriptions | No migration needed |
| Add a field with a default factory, or with no default | With an explicit named transform |
| Add an owned container | With an explicit named transform |
| Remove or rename a field, or change a supported storage type | With an explicit named transform |
| Change a reference | With an explicit named transform |
| Increase a root or owned `__schema_version__` | Yes, after record validation |
| Change constraints, nullability, or an existing default | Yes, if stored values remain valid and unchanged |
| Change custom validation and increase the owning revision | Yes, if stored values remain valid and unchanged |

Automatic operations apply to root fields and to fields inside existing embedded objects and
children. Existing field values, owned trees, references, and retained index nodes stay
as they are.

Named transforms validate and rewrite complete records through the same maintenance
lifecycle. They pin reproducible outputs, reserve final unique values, and state the
abort boundary before execution. Root keys/globals and layout changes remain unsupported.
See the [transform guide](record-transforms.md).

## Inspect the installed schema

Inspection works even when regular access would raise `SchemaMismatch`:

```python
inspection = next_db.inspect_schema(CatalogV2)
assert inspection.status == "different"
assert inspection.installed.schema_version == 1
assert inspection.declared.schema_version == 2
assert db.get(CatalogProduct, "P1").sku == "SKU1"
```

It runs in one transaction. It does not install, activate, or repair a schema, construct
model instances, or run default factories. It does not validate records or index contents.

| Status | Meaning |
| --- | --- |
| `uninstalled` | No installed schema or application data exists. |
| `unmanaged` | Application data exists without installed schema metadata. |
| `matching` | Exact identity, or a verified compatible legacy declaration. |
| `different` | Valid installed metadata differs from the declaration. |

`has_data` reports whether any node exists in the application global. It does not count
records.

Incomplete metadata, invalid canonical JSON, a mismatched global, or a bad snapshot
fingerprint raises `StorageError`. Declarations that generate nonfinite JSON numbers raise
`SchemaError`.

`SchemaSnapshot` exposes immutable canonical bytes and derived metadata. Its normal
representation leaves out schema contents. `snapshot.to_dict()` returns the full schema,
including declared defaults, as a fresh dictionary.

## Compare two schemas

Comparison works without a database:

```python
changes = compare_schemas(inspection.installed, inspection.declared)
assert any(change.kind == "field_added" and change.path == ("note",) for change in changes)
```

Changes distinguish fields, storage types, validation, defaults, indexes, relationships,
and version metadata. Paths contain declared field names, not child keys. A rename appears
as a removal and an addition.

Identity changes, destructive changes, unknown layouts, and unsupported schema shapes
require manual review. Comparison resolves local Pydantic definitions and never fetches
external JSON schemas. It does not run validators or default factories.

Normal change representations hide default values. An added scalar field with a literal
default can describe a backfill. Required fields without defaults, and fields with default
factories, require manual values.

## Review a plan

```python
plan = next_db.plan_migration(CatalogV2)
assert not plan.requires_manual_review
assert any(step.kind == "backfill_field" and step.path == ("note",) for step in plan.steps)
assert plan.steps[-1].kind == "activate_schema"
assert plan.execution_supported
assert plan.to_json() == next_db.plan_migration(CatalogV2).to_json()
```

A plan describes the changes from the installed fingerprint to the current declaration.

- Backfills come before index rebuilding.
- Schema activation comes after validation and record metadata updates.
- Unique indexes add a uniqueness check.
- Index removals produce `drop_index` steps, and uniqueness downgrades produce
  `drop_uniqueness` steps.
- Reference changes add reverse-index rebuilding and validation.
- Matching schemas produce an empty plan, and empty globals produce an installation plan.
- Unmanaged data requires manual review and is never adopted implicitly.

`to_json()` returns deterministic, compact ASCII JSON without timestamps. The
[plan fixture](../../tests/fixtures/add-field-migration-plan.json) defines the review
format. Normal output leaves out literal defaults. Use `to_dict(include_defaults=True)` or
`to_json(include_defaults=True)` to review them. Default payloads are JSON Schema
representations, not encoded native node values.

Nested backfills use declared field paths and apply only to existing owners. They never
create absent embedded objects or child records.

Manual review is required for removing fields, changing storage types, moving identities,
retargeting references, unknown formats, and unavailable default values. A changed default
does not replace values already stored.

Plans are per top-level model. Identity and reference changes can require coordination with
other models.

If another model contains `Reference[OldTarget]`, redeclare that field as
`Reference[NewTarget]` and register the new target and referrer together. Registries bind
references to exact Python declarations. When the target global and key identity are
unchanged, the referrer's persistence contract stays identical and needs no separate
migration. Target key or global changes require a reviewed data migration; rebinding the
Python class alone does not make them compatible.

Planning inspects the schema and does a bounded data-presence check. It does not scan or
validate records, run factories or validators, rebuild indexes, or write any node.

The two plan flags answer different questions:

- `requires_manual_review=False` does not certify that your data is compatible.
- `execution_supported` says whether the executor can run the plan structurally. Record
  validation still happens under maintenance, before any backfill.

`execution_blockers` lists structural reasons the executor cannot run a plan, such as
`unsupported_change:field_added:note`, without values. It also explains installation-only
and no-op plans. The same list appears in serialized plan documents.

## Stop other clients first

A running migration holds one job coordinator and a barrier on its affected model.
While the gate is held:

- Current clients get `MaintenanceActive` when an operation needs the affected model.
- Unrelated CRUD, queries, and reference resolution continue normally.
- Only one migration or rebuild can run at a time; explicit adoption also waits.
- `inspect_schema`, `plan_migration`, and `migration_status` still work.
- Inspection reports the source snapshot and active migration ID until activation.
- A fingerprint barrier makes older clients reject the migrating model.

Reference source reads and unrelated scalar patches do not resolve their targets.
New/changed references to the migrating model block, as do full saves with such
nonnull references. Removing an incoming reference or its source remains safe.
Deleting a target referenced by the migrating model still obeys restrict counts.
A mixed-model callback that needs the gated model rolls back staged writes.

Older clients honoring the previous global gate remain blocked on all models. Before
starting, stop clients that cannot handle the affected model becoming unavailable,
and stop raw YottaDB writers. Validators must be deterministic and free of external
side effects, because native transactions can run them again. [ADR 0009](../adr/0009-model-scoped-maintenance.md)
records the compatibility and contention limits.

## Run the migration

Keep the exact source model declaration available next to the target:

```python
plan = next_db.plan_migration(CatalogV2)
# Review plan.changes, plan.steps, and any declared defaults before starting.
assert plan.execution_supported
status = next_db.begin_migration(CatalogProduct, CatalogV2, plan=plan)
migration_id = status.migration_id  # Retain this ID for recovery.
while status.phase != "complete":
    status = next_db.advance_migration(migration_id, CatalogProduct, CatalogV2, batch_size=25)
assert next_db.get(CatalogV2, "P1").note is None
```

Call `begin_migration`, `advance_migration`, and `abort_migration` outside `db.atomic`.
Each one manages its own transaction.

Each `advance_migration` call commits at most one batch or phase transition. A batch holds
1 to 100 parent records. Each parent and its complete owned tree form one transaction unit.

The job moves through these passes:

1. **Validate.** Checks source records and target values, and reserves new unique values.
2. **Backfill.** Writes new fields and indexes.
3. **Verify.** Checks the results.
4. **Update.** Updates record metadata.
5. **Cleanup.** Runs only for index removals and uniqueness downgrades.
6. **Activate.** One transaction publishes the target snapshot and fingerprint, marks the
   job complete, and releases maintenance.

An empty page advances the phase, and the cursor resets for the next pass. Adding a field
that would exceed the [per-record limits](../reference/limits.md) fails validation without
changing application data.

Root and owned schema versions can increase without additive changes. A decrease
requires manual review. Validation-only plans skip backfill: the executor validates,
verifies, retags, and activates, preserving every field and index value. The historical
`backfilled` counter counts records ready for verification, including those requiring no
writes. Existing defaults remain stored values; new defaults apply to future inserts.

## Add a unique constraint

Adding `unique=True` to an existing scalar field uses the same methods.

Validation reserves every new non-null unique value under the job, using the same
normalization as ordinary writes. Several null values stay allowed. A duplicate across
batches raises `UniqueViolation` before application indexes or record metadata change, and
the failed batch and its reservations roll back together. You can then abort, correct the
source data, and start a new plan. Duplicate errors leave out indexed values and record
identities.

Backfill builds new equality entries and unique claims from the validated reservations.
Promoting an equality index adds claims without deleting or rewriting its existing entries.
Verification checks both the reservations and the active entries and claims before
activation.

Reservations hold normalized values and owner identities inside framework metadata. They
stay with completed and aborted jobs, use space in proportion to the new non-null unique
values, and do not appear in normal status output.
Use [job pruning](#retire-job-metadata) to remove them when recovery and audit retention
no longer require the job.

## Downgrade or remove an index

Changing `unique=True` to `index=True` removes unique claims and keeps equality entries and
queries. After activation, the field allows duplicates.

Removing the index declaration removes equality entries and any unique claims, and keeps the
stored field values. An equality query on that field then raises `QueryError` and never
falls back to a scan.

The update batches remove expected entries and claims by parent. Verification checks that
retained nodes still match and removed nodes are gone. Cleanup then drains the retired
namespaces, including orphan nodes, before the target schema can activate.

Each cleanup call deletes at most `batch_size` leaf nodes. During cleanup, the record
counters stay unchanged, so several calls can return the same status until cleanup finishes.
Raw writers must stay stopped. If a retired namespace reappears before activation, the job
returns to cleanup with maintenance still held.

## Change fields inside owned trees

Nested changes use dotted declaration paths such as `lots.note` and `lots.samples.reading`.

- A scalar default goes on every existing owner at that path.
- Optional embedded objects and nullable collections that are null stay null.
- Empty collections stay empty. The executor does not create children or embedded objects
  to apply a default.
- Automatic backfill rejects a default factory, even when no owner exists yet.
  A named transform can supply reproducible values and create new owners explicitly.
- Batch limits and progress counters count parents. Per-parent node and child limits are
  also enforced against the target tree before backfill.

Nested equality queries still return owning roots. After you add an indexed `note` to `Lot`
and update `Warehouse.lots` to use that declaration,
`db.query(WarehouseV2).where(**{"lots.note": None}).all()` selects warehouses with matching
lots.

A nested unique constraint covers all occurrences at its declaration path, including
different children under the same parent. The validation pass checks those duplicates, and
duplicates across parent batches.

## Resume or abort

To resume after an interruption:

1. Reconstruct `Database` with the target registry.
2. Read `migration_status(migration_id)`.
3. Keep calling `advance_migration` with the same source and target declarations.

The executor verifies both saved snapshots on every active call. Data writes and their
checkpoint commit together. A failed batch rolls back and leaves the gate held at its
preceding checkpoint, so partly migrated data never reaches callers. Normal status output
leaves out record keys. `status.to_dict(include_cursor=True)` exposes the encoded cursor.

For a validation-only plan, rejection during the initial validation pass rolls back
the failed transaction, then atomically marks the job aborted and restores source
access in a separate transaction. This includes validators that would change existing
field bytes. Restoration checks that the rejected checkpoint has not changed. If
another worker advanced it, access stays gated; inspect the status and resume.
An interruption between rollback and restoration also leaves a resumable job. Even
database writes made by a rejecting validator roll back; external side effects cannot
be undone and validators must remain pure. Storage faults remain resumable failures. Later passes
keep maintenance held on failure; resume with the exact saved declarations. Automatic
restoration never publishes partially retagged records.

`abort_migration(migration_id)` restores source access until the first update batch or
cleanup deletion commits, including after a validation failure. After that point, abort is
refused, and recovery means resuming or restoring from a backup. The same applies to
cleanup of orphan metadata in an empty application global.

For validation-only jobs, manual abort is available during the initial validation pass.
Once that pass finishes, the satisfied backfill counter requires the job to be resumed.

Do not remove the gate or fingerprint barrier by hand. Completed and aborted job metadata
stays available for inspection.

## Retire job metadata

Completed and aborted jobs retain snapshots, checkpoint keys, and uniqueness reservations.
Reservations contain normalized indexed values and owning identities; snapshots can
contain application defaults. Apply the application's retention policy to this metadata.
Remove it explicitly after completion or safe abort:

```python
while not next_db.prune_migration(migration_id, batch_size=25):
    pass
assert next_db.prune_migration(migration_id)  # Already absent is complete.
assert next_db.get(CatalogV2, "P1").note is None
```

Each call removes at most `batch_size` leaf nodes, including the checkpoint root last.
The limit is 1 through 100. Call outside `atomic`; each batch owns its transaction.
`False` means call again, and `True` means nothing remains. Interrupted calls roll back;
committed partial pruning can be reopened with only the job ID and an empty Registry.
An active job cannot be pruned, even if its checkpoint or gate is inconsistent. A
terminal job may be pruned while an unrelated job holds maintenance.

During retirement, `migration_status(id).pruning` is true. Resume and abort operations
then raise `MigrationError` stating that metadata is being pruned. Older maintenance
clients reject this optional checkpoint field. After retirement, status is unavailable.
Application records, installed schemas, active indexes, references, and other jobs remain
unchanged. This also retires terminal index-rebuild jobs.

Keep the exact source and target classes for every unfinished migration. Schema snapshots
cannot reconstruct arbitrary Python validators, so unfinished execution still requires
those bindings. Once a job completes or is safely aborted, old classes can be removed from
application migration code when the application's recovery policy permits. Pruning removes
the durable job evidence; archive it first if required by that policy.

## Revise custom validation

Function bodies cannot be fingerprinted reliably. When the persistence-relevant behavior of
a custom validator or default factory changes, the rule is to bump the owning model's
`__schema_version__`. Built-in constraints are recorded automatically, and unknown
validation metadata is rejected.

Keep the source and target declarations, review the plan, and use the migration methods
above. A revision change permits validation and retagging; it does not authorize a
transformation of existing values. Such transformations require an explicit named
[record transform](record-transforms.md).

[Schema compatibility](../schema-compatibility.md) and
[ADR 0006](../adr/0006-canonical-schema-contracts.md) define the guarantees and limits,
including canonical schema format 2 and legacy format-one databases.
