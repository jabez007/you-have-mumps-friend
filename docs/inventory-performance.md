# Inventory workflow performance

Current aggregate replacement loads and validates complete warehouses, audits
existing physical nodes, and writes only differences. Output labels aggregate_save
as physical_node_difference. The historical section predates this behavior; its
write counts do not describe the current implementation.

Current workflow fixtures use warehouse revision three and maintain warehouse and
location stored_on_hand values and their indexes. Existing-balance transfers use one
patch group, leaving a same-warehouse net-zero root total untouched while updating
both location totals. The direct quantity-only lower bound uses frozen revision
one without stored sums, so it leaves a valid dataset while intentionally excluding
that extra work. New rows identify warehouse_schema_version. Historical measurements
below identify their original revision and are not current-v3 latency claims.

Run `YHMF_BENCHMARK=1 bash scripts/test-native.sh -q` with an installed YottaDB
distribution. In addition to the existing mutation benchmark and native demo,
the disposable harness runs `python -m scripts.benchmark_inventory` from the
repository root. It emits a `YHMF_INVENTORY_BENCHMARK` JSON result containing the
environment, raw latency samples, percentiles, counts, and workload description.
Offline benchmark tests verify equivalent final states and instrumentation only.
They provide no native latency evidence.

The same disposable harness runs python -m scripts.benchmark_rollups. Its
YHMF_ROLLUP_CONTENTION_BENCHMARK report compares no sums, an indexed root sum, and
indexed root plus owned sums. Two processes increment distinct existing children
within one location or across two locations. Each fixture has 50 items per location;
three coordinated bursts contain 30 commits per worker per configuration. Connection
warmup and setup are excluded. Reports retain raw samples, callback attempts/retries,
throughput, node calls, environment, and journal mode. Final reads independently
check every quantity and total and audit the indexes.

This isolates the choice of stored totals within that synthetic workload. Native
block and index contention can occur even without shared logical totals, and a root
total is already shared across both locations. Compare all three layouts rather
than attributing every retry to the owned field. These results do not predict a
deployment's conflict rate or justify storing totals at every level. Raw native
measurements are recorded below.

## Qualified owned totals and contention

[CI run 42391564712](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42391564712)
qualified c51a7eed819ac2f256dcd964335a2342207cd3ac on r2.02 and r2.06, with
journaling off and before-image journaling on. Each complete suite passed 2,372
tests (200 skipped); each separate stress run passed 25 (four skipped). The
[contention report](benchmarks/native-owned-sum-contention-2026-11-07.json) retains
72 rows and all 4,320 timed commits. The
[inventory report](benchmarks/native-inventory-location-totals-2026-11-07.json) retains
84 rows and 2,520 samples. These workflow measurements use warehouse v3 and movement
v2; they precede the movement-v3 timestamp index. New benchmark rows identify both
schema revisions. Do not treat these as measurements of that later ledger index.

The r2.02 runner used an AMD EPYC 9V74 and Python 3.12.12; r2.06 used an Intel Xeon
Platinum 8370C and Python 3.12.3. Both used Pydantic 2.14.3. Different hosts and load
prevent attributing differences to engine versions.

For each row below, three bursts commit 180 increments. Each cell gives additional
callback attempts / pooled median milliseconds. Retry counts are not the fraction
of transactions that conflicted: one commit can retry more than once.

| Engine | Journal | Locations | No totals | Root total | Root + owned totals |
| --- | --- | --- | ---: | ---: | ---: |
| r2.02 | Off | Same | 146 / 0.216 | 159 / 0.311 | 171 / 0.369 |
| r2.02 | Off | Different | 163 / 0.216 | 165 / 0.329 | 170 / 0.390 |
| r2.02 | Before-image | Same | 108 / 0.524 | 173 / 0.774 | 133 / 0.888 |
| r2.02 | Before-image | Different | 113 / 0.483 | 152 / 0.779 | 128 / 0.888 |
| r2.06 | Off | Same | 146 / 0.433 | 148 / 0.531 | 160 / 0.769 |
| r2.06 | Off | Different | 156 / 0.403 | 148 / 0.530 | 153 / 0.643 |
| r2.06 | Before-image | Same | 157 / 0.829 | 159 / 1.322 | 163 / 1.407 |
| r2.06 | Before-image | Different | 164 / 0.832 | 145 / 1.196 | 162 / 1.470 |

Totals increase measured latency here, while retry counts do not increase
monotonically. Independent logical children already conflict on native blocks, and
both total layouts share the root total. These results support treating totals as
an explicit reporting/cost tradeoff, without predicting deployment conflict rates
or claiming location totals alone cause every retry.

With 250 lots per bin, equivalent warehouse-v3 transfers measured:

| Engine | Journal | Partial median / p95 ms | Replace median / p95 ms |
| --- | --- | ---: | ---: |
| r2.02 | Off | 1.434 / 1.536 | 153.093 / 161.594 |
| r2.02 | Before-image | 1.822 / 1.900 | 151.156 / 159.382 |
| r2.06 | Off | 2.129 / 2.168 | 228.453 / 244.859 |
| r2.06 | Before-image | 2.581 / 3.070 | 229.139 / 243.192 |

Partial transfers use 139 gets, 36 sets, four child walks, and two index leaf
deletions, unchanged as unrelated lots grow from 1 to 250. Replacement uses 11,949
gets and 2,538 child walks at 250 lots; it writes the same changed nodes. This is
evidence for bounded targeted work, not a whole-subtree deletion claim or a comparison
with another database. Direct-native rows remain the frozen-v1 quantity-only lower
bound, excluding ledger and invariant maintenance.

## Workload and method

Each fixture contains one product, 1/50/250 independent lots, and a warehouse with
two locations and one bin per location. Each bin contains one balance per lot.
The selected lot begins with 1,000 units at the dock and zero in storage; unrelated
balances remain zero. Catalog and opening balances are prepared before timing.

Each measured receive, transfer, or adjustment changes existing balances and
appends one immutable movement through the inventory service. Indexed references
and ledger entries participate in the same transaction. The partial implementation
uses owned-path reads and patches. The whole-aggregate baseline loads each affected
warehouse, groups all balance changes, and saves each root once per workflow. Both
use the same domain validation and ledger code. This avoids charging the whole
baseline for two unnecessary replacements of the same warehouse during a move.

The direct native row reads and assigns only the quantity node in a native
transaction. It excludes the ledger, schema/model validation, availability checks,
and reference/index work. It is a lower bound on bridge/storage overhead, not an
equivalent inventory workflow. Quantity has no secondary index, so this assignment
does not leave a stale quantity index.

Five warm-up operations precede 30 samples per row. Latency includes the storage
counter wrapper, Python calls, validation, transaction, and ledger work. Setup,
cleanup, and final verification are excluded. The shared measurement helper reports
median and p95; the result also includes p99 and all samples. With 30 samples, p99
is effectively a maximum and has substantial sampling uncertainty. Counters record
adapter calls, not YottaDB block writes, disk operations, or allocated bytes.

Final verification checks both balances, all unrelated quantities, movement count,
declared indexes, and independent expected inbound reference counts. The process
is single threaded with a warm disposable database. The current harness separately
supports journaling off and before-image journaling on; structured output records
the mode. Historical rows must retain their original mode. This is not a crash
recovery or production tail-latency qualification.
Different CI runners may have different CPUs and load, so results do not isolate
engine-version effects or establish comparisons with other databases.

## Scope and limitations

New child insertion retains the bounded aggregate-size validation established in
v0.4. This benchmark measures existing balances, not that cold insertion cost.
Stock-by-product queries still materialize matching warehouse aggregates. Ledger
appends update shared inbound counts, which can limit concurrency even when
quantities belong to separate children. The existing contention benchmark and
native inventory double-spend test exercise correctness separately; these serial
measurements do not establish application throughput under contention.

## Historical warehouse-v2 stored-total measurements

[CI run 42345725275](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42345725275)
qualified source ce2a4e67da7e0877b783f2bcbff3f0e4dc68e4d2 on both engines, with
journaling off and before-image journaling on. Each configuration passed 2,150 tests
(74 skipped), the native demo, and both benchmarks. Separate stress jobs passed
25 tests per engine (four skipped).
The [raw report](benchmarks/native-inventory-stored-totals-2026-11-07.json) contains
all 84 rows, 2,520 timed samples, environment metadata, and node-call counts.

The r2.02 runner used Python 3.12.12 on an AMD EPYC 7763; the r2.06 runner used
Python 3.12.3 on an AMD EPYC 9V45. Both used Pydantic 2.14.3. Different hosts prevent
attributing differences to the engine version. Five warm-ups and 30 samples per
row follow the method above; p99 values remain in the raw report.

Transfer latency in milliseconds, including one ledger append and maintaining the
warehouse-v2 stored total/index:

| Engine | Journal | Lots per bin | Partial median | p95 | Replace median | p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| r2.02 | Off | 1 | 1.663 | 1.821 | 2.909 | 3.149 |
| r2.02 | Off | 50 | 1.695 | 1.811 | 37.493 | 42.009 |
| r2.02 | Off | 250 | 1.688 | 1.828 | 179.408 | 187.123 |
| r2.02 | Before-image | 1 | 2.285 | 2.372 | 3.661 | 3.767 |
| r2.02 | Before-image | 50 | 2.297 | 2.702 | 40.506 | 42.909 |
| r2.02 | Before-image | 250 | 2.279 | 2.368 | 191.076 | 199.616 |
| r2.06 | Off | 1 | 0.961 | 0.999 | 1.761 | 1.806 |
| r2.06 | Off | 50 | 0.982 | 1.010 | 25.536 | 33.162 |
| r2.06 | Off | 250 | 1.012 | 1.052 | 126.121 | 139.310 |
| r2.06 | Before-image | 1 | 1.339 | 1.383 | 2.134 | 2.183 |
| r2.06 | Before-image | 50 | 1.342 | 1.389 | 25.190 | 31.793 |
| r2.06 | Before-image | 250 | 1.311 | 1.365 | 130.087 | 144.779 |

Observed adapter calls per transfer agree across all four configurations:

| Method | Lots per bin | Gets | Sets | Child enumerations | Tree deletions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Partial | 1, 50, 250 | 133 | 32 | 4 | 0 |
| Replace | 1 | 236 | 32 | 46 | 0 |
| Replace | 50 | 2,539 | 32 | 536 | 0 |
| Replace | 250 | 11,939 | 32 | 2,536 | 0 |

The equivalent methods write the same changed nodes. Replacement still reads and
audits unrelated children; partial transfers retain bounded reads. Both leave a
net-zero stored total unchanged, with the partial group's lack of total/index IO
also checked by a deterministic path trace. All measured commands committed with
one callback attempt; these serial runs do not measure contention.

Receive and adjustment change the total. At 250 lots per bin, their median latency
in milliseconds was:

| Engine | Journal | Receive partial | Receive replace | Adjust partial | Adjust replace |
| --- | --- | ---: | ---: | ---: | ---: |
| r2.02 | Off | 1.249 | 179.078 | 1.263 | 179.690 |
| r2.02 | Before-image | 1.723 | 184.135 | 1.820 | 183.379 |
| r2.06 | Off | 0.749 | 129.030 | 0.707 | 123.427 |
| r2.06 | Before-image | 1.078 | 126.180 | 1.073 | 128.951 |

Each partial receive/adjust used 96 gets, 28 sets, four child enumerations, and one
tree deletion at every measured size. The quantity-only direct row used one get
and one set. At 250 lots its median ranged from 4.47 to 7.38 microseconds without
journaling and 84.15 to 92.84 with before-image journaling. That lower bound uses
warehouse revision one, omits totals and ledger/index/reference maintenance, and
is not an equivalent workflow. These observations establish bounded partial work
in this fixture, not production throughput or a comparison with other databases.

## Historical measurements before node-difference saves

[CI run 42294677787](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42294677787)
qualified source `3bba6bf0b36f422f23bef1d1261be9c8936082e1` on both engines. Each
native job passed 970 tests, the native demo, both benchmark runners, and 14 separate
native stress cases. [Raw samples and environment metadata](benchmarks/native-inventory-2026-11-06.json)
are committed. The r2.02 runner used Python 3.12.12 on an Intel Xeon Platinum 8573C;
the r2.06 runner used Python 3.12.3 on an Intel Xeon 6973P-C. Both used Pydantic 2.14.3.
These host differences prevent attributing latency differences to engine versions.

Transfer latency in milliseconds, with one ledger append per transfer:

| Engine | Lots per bin | Partial median | p95 | p99 | Replace median | p95 | p99 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| r2.02 | 1 | 1.377 | 1.418 | 1.429 | 2.000 | 2.062 | 2.064 |
| r2.02 | 50 | 1.420 | 1.458 | 1.477 | 27.401 | 34.591 | 35.062 |
| r2.02 | 250 | 1.438 | 1.586 | 1.666 | 135.119 | 145.617 | 145.899 |
| r2.06 | 1 | 1.194 | 1.248 | 1.355 | 1.810 | 1.909 | 2.003 |
| r2.06 | 50 | 1.614 | 2.074 | 2.151 | 25.965 | 33.908 | 34.118 |
| r2.06 | 250 | 1.266 | 1.297 | 1.363 | 132.179 | 147.681 | 147.811 |

Receive and adjustment medians in milliseconds at 250 lots per bin:

| Engine | Receive partial | Receive replace | Adjust partial | Adjust replace |
| --- | ---: | ---: | ---: | ---: |
| r2.02 | 0.937 | 134.941 | 0.938 | 134.838 |
| r2.06 | 0.830 | 129.785 | 0.872 | 129.677 |

Observed adapter calls per transfer were identical across both engines:

| Method | Lots per bin | Gets | Sets | Child enumerations | Tree deletions |
| --- | ---: | ---: | ---: | ---: | ---: |
| Partial | 1, 50, 250 | 133 | 32 | 4 | 1 |
| Replace | 1 | 201 | 59 | 14 | 2 |
| Replace | 50 | 2,014 | 549 | 14 | 2 |
| Replace | 250 | 9,414 | 2,549 | 14 | 2 |

The partial method's one tree deletion addresses the newly inserted movement
record; it does not delete an inventory subtree. The replacement method also
replaces the warehouse. All serial commands committed with one callback attempt.
The direct quantity-only lower bound used one get and one set, with median latency
between 5.56 and 5.82 microseconds across these fixtures. It did not append a ledger
entry and must not be presented as an equivalent workflow.

These measurements show bounded partial-mutation work as unrelated balances grow
within this workload. They also show substantial fixed Python/validation/ledger
cost above a direct assignment. They do not establish production throughput,
journal durability cost, query-projection performance, or performance against Odoo,
PostgreSQL, or any other database.

## Bin aggregate boundary control

The legacy warehouse layout's structural capacity walk grows with unrelated bins.
The independently rooted BinInventory example uses per-bin capacity and stock instead.
Run python -m scripts.benchmark_bin_boundary inside the disposable native harness; it
is included in both native CI versions and journaling modes. The report prefix is
YHMF_BIN_BOUNDARY_BENCHMARK. It contains every sample, median/p95, runtime/library
versions, and Python node calls per insert/delete cycle at 2 and 1,000 bins.

The workload inserts one positive balance with lot/product references into an empty
selected bin, then deletes it in a separate transaction. All other bins are empty.
Three untimed cycles warm source loading and the digest cache; seeding, compilation,
process launch, and independent paginated index/reference audits are outside timings.
The Balance-root alternative is script-only. It indexes a reference to its position;
Warehouse and Bin instead maintain their own declared stored sums. Those are different
bookkeeping contracts, so absolute timings are not equivalent application workflows.
This control isolates growth from unrelated bins, not receive/move/adjust throughput.
The new service also validates its selected bin contents, so its own cost can grow
with that bin's balances. The qualified native observations follow.


[CI 42432641592](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42432641592)
passed on r2.02/Ubuntu 22.04 and r2.06/Ubuntu 24.04, each with journaling off and
before-image journaling on. Each configuration passed 2,739 tests (435 skipped),
and each engine's separate stress job passed 25 tests (four skipped). The
[raw report](benchmarks/native-bin-boundary-2026-11-07.json) contains four environments,
48 rows and 720 measured samples at source 90e2f3f. Skips include memory fixtures
that cannot share a database with native workers. Python and Pydantic versions,
CPU and OS details are included per environment.

Values below are median (p95) **milliseconds per insert/delete cycle**. Each cell
shows 2 bins / 1,000 bins; every case has 15 measured cycles after three warmups.

| Engine | Journal | Client | Warehouse 2 / 1000 | Bin 2 / 1000 | Balance 2 / 1000 |
| --- | --- | --- | --- | --- | --- |
| r2.02 | off | python | 0.888 (0.913) / 13.010 (13.356) | 0.646 (0.684) / 0.628 (0.650) | 0.656 (0.664) / 0.646 (0.672) |
| r2.02 | off | m | 0.855 (0.903) / 19.409 (20.158) | 0.599 (0.644) / 0.613 (0.645) | 0.270 (0.319) / 0.292 (0.329) |
| r2.02 | on | python | 1.803 (2.145) / 13.909 (14.389) | 1.375 (1.755) / 1.562 (1.714) | 1.476 (1.691) / 1.507 (1.878) |
| r2.02 | on | m | 1.730 (1.959) / 19.616 (20.230) | 1.406 (1.649) / 1.524 (1.704) | 1.056 (1.789) / 0.990 (1.284) |
| r2.06 | off | python | 1.070 (1.165) / 16.329 (17.655) | 0.862 (0.940) / 0.769 (0.796) | 0.822 (0.882) / 0.899 (1.010) |
| r2.06 | off | m | 0.897 (0.943) / 20.286 (21.873) | 0.666 (0.709) / 0.693 (0.729) | 0.329 (0.440) / 0.359 (0.437) |
| r2.06 | on | python | 1.919 (2.070) / 16.910 (18.509) | 1.510 (1.675) / 1.582 (1.740) | 1.575 (2.572) / 1.563 (1.738) |
| r2.06 | on | m | 1.729 (2.030) / 20.641 (21.294) | 1.318 (1.449) / 1.513 (1.763) | 0.945 (1.077) / 1.035 (1.116) |

The Python bin cycle uses exactly two transactions/attempts, 64 gets, five children
calls, 19 sets and seven delete_tree calls at both sizes in all four environments.
This is measured native adapter work, not a memory-only timing inference. Both
clients avoid the legacy warehouse-wide structural traversal. Journaled bin medians
vary more despite identical node work; do not interpret small timing differences as
an asymptotic change.

The Balance-root M alternative is faster in this workload, but does not maintain a
bin-owned collection count and local stored total. Its Python costs are similar to
Bin ownership here. Bin ownership retains direct hierarchical inspection/enumeration
and local sum maintenance, which the application needs. No claim is made about a
Balance-root design with equivalent cross-root totals, or about Odoo/PostgreSQL.

These are short, single-worker runs in fixed workload order on shared CI runners.
p95 with only 15 samples is the largest observation, not a robust tail estimate.
The workload has empty unrelated bins, warm location digests, no contention and one
balance per timed cycle. It does not measure saturated bins, ledger workflows or
multiple-process throughput. Reference-count and native block contention remain,
as the separate M digest controls demonstrate. The test fixture independently stores
1,600 balances across two bins; that capacity evidence is distinct from this timing
workload. Preserve per-bin budgets and measure application-specific distributions
before drawing broader conclusions.

The later [application qualification run 42432731997](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42432731997)
also passed the new bin-layout M receipt, including exact Python node equivalence,
ordered history lookup, duplicate-ID/invalid-input rollback, forced restart, competing
receipt recalculation and the move checkpoint. On both engines and journaling modes,
that complete suite passed 2,752 tests (450 skipped); separate native stress passed
25 (four skipped). The offline suite passed 1,259, and both Pydantic compatibility
jobs passed 837. Source 0f2b462 includes the final application code; subsequent changes
align documentation and publish these observations.
