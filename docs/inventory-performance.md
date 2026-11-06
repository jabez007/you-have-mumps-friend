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

Native measurements and their exact source commit will be added after the benchmark
has run in both qualified CI environments.
