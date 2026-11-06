# Native mutation benchmark qualification

Observed on 2026-11-06 in [CI run 42262595757](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42262595757),
commit `cbe64797753bd1ee6fdee57036674554a3c05b4a`.
The [machine-readable results](benchmarks/native-mutations-2026-11-06.json) include
environment details, median/p95 summaries, and operation counts.

## Workload

A root warehouse contains one bin and an increasing number of lots. Each lot has
a string key and an unindexed, nonnegative Decimal quantity. Each timed operation
changes one existing quantity. Five warmup iterations precede 40 measured samples
per method/size. Setup and result assertions are outside the timed interval.

- Direct: one `NativeStore.atomic` transaction with scalar encoding and one node write.
- Patch: `db.patch` with the deep owned path, metadata checks, and complete leaf validation.
- Replace: `db.atomic`, full root `get`, change quantity, then whole-root `save`.

Latency includes Python execution and the same adapter counting wrapper in every
method. P95 uses the sorted sample at zero-based index `floor(0.95 * N)`; tails are
coarse at this sample size. No outliers are discarded. There are no concurrent
writers during the benchmark. Separate native tests qualify races and restarts.

## Measured median latency

Values are microseconds per committed change. Each engine ran on its own GitHub
Actions runner; this table does not isolate engine-version effects.

| Environment | Lots | Direct | Patch | Whole replacement |
| --- | ---: | ---: | ---: | ---: |
| r2.02 / Ubuntu 22.04 | 1 | 2.01 | 90.59 | 191.75 |
| r2.02 / Ubuntu 22.04 | 10 | 2.06 | 89.90 | 483.94 |
| r2.02 / Ubuntu 22.04 | 100 | 2.07 | 89.93 | 3,321.32 |
| r2.02 / Ubuntu 22.04 | 1,000 | 1.96 | 87.94 | 32,465.63 |
| r2.06 / Ubuntu 24.04 | 1 | 3.65 | 173.85 | 348.49 |
| r2.06 / Ubuntu 24.04 | 10 | 3.74 | 165.01 | 951.45 |
| r2.06 / Ubuntu 24.04 | 100 | 3.60 | 165.22 | 6,027.68 |
| r2.06 / Ubuntu 24.04 | 1,000 | 3.44 | 154.13 | 60,318.86 |

## Observed storage work

Both environments reported these exact adapter operation counts per change:

| Method | Lots | Gets | Sets | Child enumerations | Tree deletes | Transactions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct | All sizes | 0 | 1 | 0 | 0 | 1 |
| Patch | All sizes | 15 | 1 | 0 | 0 | 1 |
| Replace | 1 | 46 | 12 | 4 | 1 | 1 |
| Replace | 10 | 100 | 39 | 4 | 1 | 1 |
| Replace | 100 | 640 | 309 | 4 | 1 | 1 |
| Replace | 1,000 | 6,040 | 3,009 | 4 | 1 | 1 |

Patch latency stayed approximately flat while whole replacement grew with the
unrelated lot count. Direct assignment remains substantially cheaper than the
framework operation. The latter also verifies ownership, installed metadata, and
model constraints, so these results quantify the cost of that additional work.

## Limits and follow-up

This is a warm, uncontended microbenchmark on ephemeral virtual runners, not a
production throughput claim. CPU model, persistence/fsync settings, cold-cache
behavior, index-heavy workloads, and sustained contention are not characterized.
Actual Python versions were 3.12.12 for r2.02 and 3.12.3 for r2.06; both used
Pydantic 2.14.3. See the JSON for reported platform and YDBPython release strings.

The benchmark covers scalar patches. Child insertion has an aggregate-size check
for layout-one node limits; it is not a constant-time operation. Ancestor custom
validators require full aggregate operations and are explicitly unsupported for
partial mutations. Optimistic revisions and a successor fingerprint format are
deferred. Profile native Python callback overhead and add indexed/contended
benchmarks before pursuing further optimization; no native extension is justified
by these measurements alone.

## Reproduce

Install a supported native runtime and run:

```sh
YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
```

This uses a newly created disposable database and prints `YHMF_BENCHMARK` JSON
after the tests. No production database is involved.
