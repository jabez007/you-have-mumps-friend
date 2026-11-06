# Inventory workflow performance

Run `YHMF_BENCHMARK=1 bash scripts/test-native.sh -q` with an installed YottaDB
distribution. In addition to the existing mutation benchmark and native demo,
the disposable harness runs `python -m scripts.benchmark_inventory` from the
repository root. It emits a `YHMF_INVENTORY_BENCHMARK` JSON result containing the
environment, raw latency samples, percentiles, counts, and workload description.
Offline benchmark tests verify equivalent final states and instrumentation only.
They provide no native latency evidence.

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
is single threaded with a warm disposable database. The harness does not configure
journaling; this is not a durability or production tail-latency qualification.
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

## Qualified native measurements

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
