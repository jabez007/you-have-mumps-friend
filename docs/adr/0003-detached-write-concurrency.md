# ADR 0003: Detached saves and transactional read/modify/write

Status: accepted for v0.3.1; detached revision/tracking decisions revisited in issue 45.

`get` returns a detached validated value. `save` is an intentional complete
replacement/upsert, with last-writer-wins semantics. Reading the same version in
two processes and then saving both increments can leave one increment, although
each individual save is atomic and indexes/references remain consistent.

The existing `Database.atomic` callback is the safe read/modify/write interface:
read the record inside the callback, change that attempt's object, then save it
through the same transaction handle. Native TP restarts rerun the callback with a
fresh handle. A retry must not reuse a detached object captured before the
callback. Callbacks can run multiple times and must avoid external side effects.

Coordinated native process tests force both readers to see the same starting
state, commit one update, then release the other. Detached saves finish at one;
atomic increments finish at two. Existing forced-restart and multi-process tests
also check retry-local state and transaction-handle lifetime.

Optimistic revision checking could expose lost updates as explicit conflicts, but
would add a stored record revision, detached revision tokens, and policies for
nested ownership, root replacement, migrations, restarts, and older writers.
Adding a revision field without coordinating old clients would give a false
consistency guarantee. Defer that feature beyond v0.4. Fine-grained mutations
instead read their affected owner inside each transaction attempt and preserve
unrelated nodes. Same-field patches serialize with the later committed mutation
winning; arbitrary arithmetic still belongs in an atomic read/modify/write.

## Decision after the inventory workload

Keep optimistic revisions and automatic change tracking deferred as separate
features. This is a decision about demonstrated requirements, not an assurance that
detached saves detect conflicts. No revision field or implicit tracking is added.

[Inventory.receive, move, and adjust](../../examples/inventory/ledger.py) read
balances inside atomic callbacks. Existing balances use patch groups; missing ones
use explicit child insertion. These workflows do not edit detached copies. Native
[detached/atomic tests](../../tests/test_native_schema_safety.py) still demonstrate
one lost detached increment versus two committed atomic increments. The
[replacement benchmark](../performance.md) and targeted mutation tests show
why intentional whole replacement and partial mutation remain distinct operations.

### Optional optimistic checks

Reconsider an explicit compare-and-replace API when an application needs a detached
editing session and can define its conflict unit: root aggregate, owned record, or
field. Preserve intentional save/upsert rather than silently changing it into CAS.
A root revision written by every child mutation would create a shared conflict
point for otherwise independent children. Per-owner tokens need rules for parent
replacement, child deletion/recreation, and migrations. Snapshot comparison avoids
a stored counter but requires a bounded read and a decision about ABA changes.

Any revision implementation must define all managed writer paths: insert/save,
patch/grouped patches, child insertion/deletion, cascades/set_null, transforms,
adoption, and both owning and participating generated M entry points. Revisions
must be read and checked inside each retry, and rolled back with data and indexes.
Deployment needs an explicit activation/migration barrier that refuses older
managed writers; silently installing counters would let an older writer bypass
the guarantee. Exported routines must be regenerated and bind that contract.
Unmanaged raw M writes remain outside ORM guarantees and must follow the contract
explicitly. They cannot be made safe by a process-local token cache.

### Automatic tracking

Keep Pydantic models as detached values without persistence side effects. Current
save validates and audits the complete desired aggregate but writes only physical
node differences. Tracking Python assignments would not by itself prove model
validators, indexes, references, deletions, or raw extra nodes remain valid.
In-place collection edits, copied objects, defaults, and transaction restarts would
need a new object lifecycle. The inventory already expresses changes with patch
and patch_many, so that lifecycle would not remove a demonstrated workaround.
Reconsider only with a concrete downstream editing workflow and measurements that
separate validation, physical auditing, and diff costs. Tracking must remain
independent of optional conflict detection.
