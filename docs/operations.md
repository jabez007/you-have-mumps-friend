# Operate a qualified evaluation

Use the exact artifact, commit, environment and upgrade path in the qualification
record. Candidate qualification is evidence for evaluation, not a production SLA.
See [qualification](release-qualification.md) for the matrix and
[compatibility](compatibility.md) for contracts.

## Install and select the database

Install YottaDB separately using the native setup guide and upstream installer.
The Python native extra installs YDBPython only. An unpacked engine directory without
its required installed privileged helper is insufficient. Keep engine libraries,
ydb_dist, ydb_gbldir, ydb_routines and LD_LIBRARY_PATH consistent in every process.
Use the qualified Linux x86-64 environments before evaluating other combinations.
Review the exact engine/driver notices in the component license guide.

Map application __global__ names to intended YottaDB regions through the operator's
GDE configuration. Keep ^YHMFSchema, ^YHMFIndex, ^YHMFUnique, ^YHMFRef and ^YHMFMigration
with the corresponding application database. They are authoritative bookkeeping,
not disposable caches. Native setup documents the qualified 1,019-byte key and
32,767-byte record settings. Larger schema snapshots need adequate region records.
Combined encoded key/value sizes and transaction buffers still impose engine limits.
YHMF reports native size failures; it does not resize regions or configure privileges.

The first managed write installs metadata only against an empty unmanaged global,
inside the same transaction. Existing globals need explicit adoption or migration.
Inspect schemas before application startup/cutover and fail on unknown metadata.
Raw global access and GlobalView do not adopt a database or maintain managed indexes.
Never point the qualification harness at an application database; it creates its own
fresh directory/database and removes them on exit.

## Processes and transactions

Use process-level concurrency with YDBPython. Upstream's Python binding does not
provide the threaded C API functions. See the
[Python concurrency guidance](https://docs.yottadb.com/MultiLangProgGuide/pythonprogram.html#python-concepts).
A process owns its adapter/registry. Do not carry an active transaction into another
process or assume a Python lock coordinates other writers.

Read dependent values inside atomic. YottaDB can repeat the callback, so use stable
request IDs and timestamps, recreate mutable attempt state, and keep external effects
outside the callback. save of a detached snapshot remains last-writer-wins. Fine-grained
patch avoids overwriting unrelated fields but same-field contention still serializes
through native transactions. Generated M participants require a restartable outer TP
and roll back the whole caller transaction on failure. Follow the transaction/M guides.

## Journals, backups and recovery

YHMF maintains logical transactional invariants. Operators configure durability,
retention, permissions, journal storage, replication and recovery. Upstream distinguishes
transaction atomicity from durability through journaling. The
[journal guide](https://docs.yottadb.com/AdminOpsGuide/ydbjournal.html) defines engine
recovery requirements. Journal-enabled CI is not a power-loss or restore exercise.

Use a coordinated engine-supported backup of all participating regions and retain
matching global directory, configuration, journal chain, artifacts, model bindings and
M sources. Rehearse restore in a separate environment with isolated database/journal
paths and verify application invariants there. A live file copy is not proof of a
consistent backup. Follow the exact engine version's
[MUPIP backup/restore procedures](https://docs.yottadb.com/AdminOpsGuide/dbmgmt.html#backup).
Do not reopen a restored copy with journal paths still targeting the live database.

Restore drills, journal recovery after power loss, replication/failover and deployment
access controls remain application/operator qualification. No such drill was performed
by this release-engineering work. Follow the cutover runbook for logical migrations.

## Performance boundaries

| Decision | Practical consequence and evidence |
| --- | --- |
| Ownership | Keep independently changing/lifetime entities in separate roots. The bin boundary removes warehouse-wide budget walks. At 1,000 empty bins the measured native Python balance cycle was 0.63–0.77 ms versus 13–16 ms for warehouse ownership without journaling. See inventory-performance.md for environment, samples and different bookkeeping contracts. |
| Aggregate size | Whole reads/save audit the aggregate even when physical writes use a node difference. Per-root node/depth/key limits are safety budgets, not promises that a full transaction fits engine buffers. |
| Partial mutations | Patch/child operations address affected owners and bookkeeping. Structural budget checks can traverse branches for variable-width owners. A selected bin/application validator can still read that bin's balances. |
| Indexes | Equality, ordered and unique entries add reads/writes and claims. Unchanged tokens retain claims. Query roots/occurrences/pages have different units and visit limits. Missing indexes raise rather than falling back to a scan. |
| Shared aggregates | Stored ancestor sums, c:N and incoming-reference counts can be hot keys. Different roots can also share native blocks and restart each other. There is no contention-free ownership guarantee. |
| Transactions | Large atomic workflows can exhaust native buffers. Retries repeat validation/work. Keep callbacks bounded and side-effect free; measure attempt counts, not just committed throughput. |
| Queries | Follow explain, root/occurrence/page bounds and continuation cursors. Paginating reports does not create a single snapshot across pages. Never turn a bounded all into an implicit unbounded report. |

Use the reproducible [native workloads](performance.md) and
[inventory measurements](inventory-performance.md).
They contain raw samples, node operations and contention controls. Results from warm,
single-worker synthetic runs do not predict arbitrary workloads or other databases.
MemoryStore measurements establish work scope, not native throughput. There is no
new performance threshold or engine rewrite in this milestone.
