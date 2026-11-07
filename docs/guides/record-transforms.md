# Transform records during a schema migration

Use a named Transform when changing existing values or ownership structure.
Ordinary literal-default and validation-only migrations keep their existing behavior;
they never start transforming data automatically.

## Review and run

Keep the exact source and target declarations and a pure conversion function:

```python
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry, Transform


class Before(Model):
    __global__ = "^TransformArticle"
    id: str = Field(primary_key=True)
    caption: str = Field(index=True)
    quantity: int


class After(Model):
    __global__ = "^TransformArticle"
    __schema_version__ = 2
    id: str = Field(primary_key=True)
    label: str = Field(index=True)
    quantity: str


def convert(value: Before) -> After:
    return After(id=value.id, label=value.caption, quantity=str(value.quantity))


transform = Transform("article.rename-and-quantity.v1", convert)
store = MemoryStore()
old = Database(store, Registry(Before))
old.insert(Before(id="A", caption="Sample", quantity=3))
db = Database(store, Registry(After))
plan = db.plan_migration(After, transform=transform)
assert plan.execution_supported
assert plan.requires_manual_review and plan.destructive
assert plan.to_dict()["abort_boundary"] == "first_committed_index_delete_or_record_rewrite"

# Review the plan and arrange backups/writer coordination before beginning.
status = db.begin_migration(Before, After, plan=plan, transform=transform)
while status.phase != "complete":
    status = db.advance_migration(
        status.migration_id, Before, After, transform=transform, batch_size=25
    )
assert db.get(After, "A").label == "Sample"
assert db.get(After, "A").quantity == "3"
assert db.query(After).where(label="Sample").all()[0].id == "A"
assert db.check_indexes(After).issues == ()
```

The root global, primary-key field, and key type must stay the same. Every converted
record must retain its encoded identity. Root identity changes, layout upgrades,
schema revision downgrades, and unknown schema shapes remain unsupported. A data-only
behavior change needs an explicit application revision so it has a reviewed schema
difference.

The callable must return the exact target model. YHMF revalidates it and enforces the
normal node, child, codec, index, and reference limits. Copy fields explicitly when
virtual computed output or custom presentation serialization would make model_dump
unsuitable as input. Register the target's exact reference declarations as usual.

## What can change

A transform can rename/remove fields, convert supported storage types, supply required
values or deterministic factory defaults, construct embedded objects/keyed children,
and retarget references. Its result is the complete desired aggregate. Obsolete nodes
are removed during the rewrite. It may exchange unique values between records; all
final values are reserved during validation before the model's old indexes are cleared.
Other models' index namespaces remain untouched.

Factory defaults and conversion functions must produce the same encoded result when
retried. Fix timestamps, random values, and external inputs before migration, or derive
them deterministically from stored source data. A changed transform body or dependency
requires a new application-owned name for a new job. Names identify approved code;
the database neither stores functions nor hashes their implementations.

## Phases and abort boundary

1. Validate source/target records and current secondary structures, check proposed
   reference targets, reserve final unique values, and pin source/target byte digests.
2. Clear this model's equality/unique index leaves in bounded transactions.
3. Transform each root in one transaction, maintaining reverse edges/counts and building
   its new indexes. The output must match the validated digests. Source record metadata
   remains in place while the model is gated.
4. Verify target data, digests, references, indexes, and reservations.
5. Retag records, then atomically activate the target snapshot and release maintenance.

Batch sizes are 1–100 roots, except index clearing which counts leaves. Each root still
has the normal record-size bound. Status counters retain their existing four-pass
meaning: validated, backfilled, verified, retagged. A custom operation document pins
the transform name and digest format using the existing operation fingerprint.

Abort is allowed until the first index deletion or transformed-record batch commits.
After that, recovery means resume or restore from backup. This boundary appears in
the review plan even if the first destructive action will be index clearing.
No source aggregate copies are retained for an automatic undo.

## Resume safely

Recreate the target registry and supply the same source, target, and named Transform
on each advance call. A failed batch rolls back all its writes and retains its previous
checkpoint. An exception from the conversion function also leaves maintenance held;
there is no automatic commit of an error result. Database effects from a rejected
function roll back, while external side effects cannot be undone. Functions and
validators must remain pure and restart-safe.

Committed validation pins exact source and target field bytes per key. A changed source
or different recomputed output is rejected before rewriting that record. Changed code
that produces identical output cannot be distinguished, and this is not an arbitrary
side-effect detector. Restore the approved declarations/code/configuration to resume
an interrupted job; changing the name does not rebind an active job.

Unrelated CRUD follows [model-scoped maintenance](migrations.md#stop-other-clients-first).
Raw writers must stay coordinated. Older clients reject the custom-operation marker
instead of resuming it through an incompatible executor. Existing v0.3/v0.4 metadata
can be a source binding; records are converted only through this explicit procedure.
Direct M readers must honor the affected global's maintenance barrier or be stopped:
rewritten fields can coexist with source record metadata before retagging. The raw
storage interface does not automatically hide this maintenance state.
Use normal terminal [job pruning](migrations.md#retire-job-metadata) after retaining
the job evidence according to the application's recovery policy.
