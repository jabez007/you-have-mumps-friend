# Cut over a schema or stock layout

Use this runbook on a rehearsed copy before changing application data. Framework
schema jobs and the inventory application's copy/clear are different operations.
Neither a Python boolean nor a maintenance marker proves every external writer stopped.

## Preflight

1. Record current/candidate artifact hashes, engine/driver/Pydantic versions, global
   directory and region mapping, journal settings and application schema inspections.
   Retain frozen source declarations and exact named transforms for active jobs.
2. Review the actual migration plan, supported operation and abort boundary. Finish
   earlier jobs against their original bindings. Do not mutate fingerprints, edit job
   manifests or substitute a changed function under the same transform name.
3. Take a coordinated engine backup with its configuration/journals and test a restore
   in an isolated environment. Record the point from which restoring would discard
   later writes. Keep old binaries and generated M source alongside that backup.
4. Inventory writers: Python workers, schedulers, command-line tools, M jobs/terminals,
   generated routines, raw Store/global writers and external triggers. Stop intake and
   drain active work through the deployment/process supervisor. Confirm the running
   versions and absence of in-flight writers operationally. A sampled process list
   alone is not a lock or a proof that a scheduler cannot restart a writer.
5. Keep the maintenance window open through validation and cutover. Restrict service
   startup with the deployment's existing controls. Do not add an advisory lock that
   raw or older clients never check and then treat it as fencing them.

## Framework schema jobs

Use begin/advance/status/abort outside atomic, per the [migration](migrations.md)
and [record-transform](record-transforms.md) guides. Modern Python/generated M clients enforce the live model-scoped barriers;
older compatible clients may use a conservative global barrier. Other models can
remain usable within that contract. Raw SET/KILL, incompatible old routines, direct
Store access and application business workflows are not universally fenced.

Record the job ID and phase after every committed batch. Reopen with the same registry,
frozen source/target bindings and exact named transform after interruption. Use the
public abort operation only before its documented irreversible boundary. After committed
rewrites/index deletion, resume or restore the coordinated backup. Reinstalling old
Python code cannot undo data transformation. Do not clear a barrier to make a stuck
job disappear. Investigate its actual failure and preserve the journal/checkpoint.

## Inventory bin move

Upgrade Movement through its explicit revision-4 transform first when applicable.
The bin move is an application copy/clear across globals; a framework Transform cannot
fan one warehouse out into independent roots. Stop all stock/setup writers, then call
begin_bin_move(db, writers_stopped=True) with BIN_MODELS and empty destination globals.
The boolean is the operator's assertion only. Repeat advance_bin_move with the same
assertion until the checkpoint is complete.

Each committed step copies one bounded warehouse, clears its legacy children and
advances the cursor atomically. Failure restores that entire step. After any committed
warehouse, finish or restore backup; the mover has no reverse conversion. A native
buffer/key-limit failure requires resolving that constraint before resuming, not a raw
partial copy. Do not start new warehouses behind the checkpoint cursor during the move.

Current cooperating application entry points add these safeguards:

- BinInventory blocks stock reads/mutations while the checkpoint is incomplete.
- Current legacy Inventory stock mutations refuse any checkpoint presence, during and
  after the move, even when the legacy registry does not register the checkpoint model.
- Current YHMFRECEIPT receive refuses checkpoint presence. receivebin validates the
  managed checkpoint/schema and requires completion before bin stock writes.

Those guards run in the stock transaction. They do not protect older application
binaries, previously copied routines, raw writers or generic Database/generated-M
calls that bypass the service. Stop and retire those clients explicitly. No process is
terminated, access grant revoked or distributed lock acquired by writers_stopped=True.

## Validate and reopen

1. Check migration/checkpoint completion and installed schema integrity. Verify root
   identities, selected records, indexes/unique claims and reference restrictions.
   Paginate audits beyond their per-call limit; the first page is not a full audit.
2. For the bin move, verify destination location/bin counts and balance quantities,
   source shells with empty locations/zero legacy sums, and unchanged movement IDs/
   history. Compute current warehouse totals by explicit bin pages, not legacy fields.
3. Regenerate bound M routines with the selected artifacts and target registry. Execute
   an application transaction from Python and, if used, M on the restored rehearsal
   database, including failure/rollback. Keep byte/encoding observations with evidence.
4. Deploy only the new service/routine versions and release intake deliberately. Observe
   schema/maintenance errors, retries and invariant checks. Keep old stock writers
   disabled after switching to bins. Rollback after new writes needs an explicit data
   recovery decision; it is not a package reinstall.

See [operations](../operations.md) for engine responsibilities and
[candidate preparation](../releasing.md) for artifact identity.
