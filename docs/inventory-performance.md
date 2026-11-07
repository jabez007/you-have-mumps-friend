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
measurements will be linked after the new workload actually executes in CI.

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
