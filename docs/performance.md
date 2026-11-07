# Native mutation benchmark qualification

The historical rows below used full tree deletion and rewrite for save. The
development branch now applies physical node differences after a bounded audit;
new benchmark output identifies aggregate_save as physical_node_difference. Older
timings are not measurements of the new implementation. The change targets write
amplification, and additional physical reads can increase replacement latency.

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

The development benchmark also reports `insertions`: 40 timed insertions after five
warmups, for 1, 10, 100, and 999 existing flat lots. The final size leaves room for a
new child under the 1,000-child limit. Patterns grow either the target collection or
an unrelated collection under the same root. Cleanup is outside timing and counters;
the final aggregate and indexes are checked independently of the timed interval.
Methods compare direct node writes, `insert_child`, and atomic root read/save. The
direct baseline maintains object/count markers but omits mapper validation, metadata,
limits, and secondary integrity checks. Both supported engines executed these workloads in
[CI run 42312220389](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42312220389),
source `73b87d1510aa873ab5c488a2ce445e77f6cec7f4`. The
[complete insertion results](benchmarks/native-child-insertion-2026-11-06.json) include
environment, median/p95, and node-operation counts.

Insertion now counts flat child collections using their transactional c:N markers and
declared field width. It descends only into variable-width owned containers when
checking the 10,000-node budget. This avoids sibling reads for fixed-width collections
without changing the layout. It trusts mapper-maintained counts; full reads still
verify count/key agreement. This optimization does not establish a particular native
conflict rate or make detached aggregate replacement safe against stale saves.

Install a supported native runtime and run:

```sh
YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
```

This uses a newly created disposable database and prints `YHMF_BENCHMARK` JSON
after the tests. No production database is involved.


## Indexed, concurrent, and profiling follow-ups

The version-two benchmark output adds equality-indexed quantity updates, unique
serial changes, and indexed reference changes. The tracked lot contains all three
kinds of secondary data so the unchanged entries exercise the same declared model.
The direct node baseline is limited to unindexed data; indexed cases compare
partial patch with atomic whole replacement, both maintaining their invariants.
All serial cases retain the same sizes, five warmups, and measured sample count.

Contention bursts use four independent native processes by default. A controller
waits until each worker has warmed its connection, releases all workers, and waits
for their measured loops. The patterns are separate roots, separate children of
one root, and one shared child. Every operation increments quantity through a read
inside `atomic`. Both patch and replacement run on unindexed and indexed models.
The default burst uses 100 lots and 40 commits per worker. Startup and state reset
are excluded; reported aggregate throughput includes the controller's release and
result-collection overhead. Worker latencies measure the operation itself.

Operation counters distinguish committed calls from callback attempts. Their
difference measures callback retries, not all internal engine restart events.
Final assertions check every quantity, unaffected children, index audits, and
reverse-reference counts. A failed assertion or worker failure fails qualification.
Different logical roots can still conflict on physical YottaDB blocks or shared
index pages; the experiment measures that behavior without assuming isolation by
root key. Contention retry counts and timings are descriptive and have no pass
threshold.

Separate cProfile runs capture the Python/native call path without the operation
counting wrapper. Profiles cover each patch workload and an indexed whole
replacement. They report call counts, self time, and cumulative time. Profiled
timings are instrumented and must not be substituted for unprofiled latency, and
nested cumulative times must not be summed. Environment output records CPU model,
logical CPU count, runtime versions, and the CI source commit.

All these workloads run through the existing disposable native database harness.
Offline workload tests establish behavior and accounting only. Both native environments executed the expanded workloads in
[CI run 42278547820](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42278547820),
commit `37013c13a74b10eacd2b68d84d7034eb6ccb4e55`. Each ran 852 tests successfully before the benchmark.
The [version-two results](benchmarks/native-followups-2026-11-06.json) preserve the
complete latency, operation-count, contention, and profile summaries.

### Indexed serial results

Median microseconds per change at 1,000 lots:

| Engine | Workload | Patch | Whole replacement |
| --- | --- | ---: | ---: |
| r2.02 | indexed | 191.50 | 213,108.52 |
| r2.02 | unique | 200.20 | 211,791.23 |
| r2.02 | reference | 282.62 | 214,032.38 |
| r2.06 | indexed | 302.63 | 318,013.39 |
| r2.06 | unique | 293.94 | 315,574.74 |
| r2.06 | reference | 425.10 | 314,034.86 |

Both engines observed constant serial patch node work across all tested sizes:

| Workload | Reads | Writes | Tree deletes | Child enumerations |
| --- | ---: | ---: | ---: | ---: |
| Equality quantity | 19 | 2 | 1 | 0 |
| Unique serial | 21 | 3 | 2 | 0 |
| Reference | 32 | 5 | 2 | 2 |

### Concurrent increments

Four processes committed 40 increments each, for 160 commits per burst, using 100
lots per root. Entries below show commits/second and callback retries in parentheses.
The roots pattern has four roots; the other patterns share one root.

| Engine | Workload | Method | Separate roots | Separate children | Same child |
| --- | --- | --- | ---: | ---: | ---: |
| r2.02 | unindexed | patch | 3,300.88 (0) | 2,201.71 (196) | 1,852.03 (354) |
| r2.02 | unindexed | replace | 97.98 (430) | 97.26 (450) | 96.55 (437) |
| r2.02 | indexed | patch | 1,682.50 (239) | 1,319.15 (347) | 1,349.99 (373) |
| r2.02 | indexed | replace | 24.46 (442) | 24.08 (452) | 24.52 (442) |
| r2.06 | unindexed | patch | 2,605.92 (0) | 1,124.91 (199) | 1,146.79 (196) |
| r2.06 | unindexed | replace | 120.14 (200) | 110.83 (210) | 117.77 (191) |
| r2.06 | indexed | patch | 1,080.96 (108) | 1,073.04 (195) | 1,091.27 (194) |
| r2.06 | indexed | replace | 23.94 (302) | 23.56 (315) | 24.96 (285) |

Every burst preserved all increments and passed index and reverse-count checks.
Separate roots eliminated observed retries for the unindexed patch case in this
run. Indexed separate roots still retried, consistent with shared index-page
contention; that explanation is an inference, not a block-level trace. Root
separation therefore does not establish a zero-contention guarantee.

### Profile findings

Both runners reported AMD EPYC 9V74 processors and four logical CPUs. Runtime
versions and platform strings are in the JSON. Each patch profile executed 25
updates at 1,000 lots; the whole-replacement profile used 100 lots.

`owned._has_functions` made 2,550 calls, including recursive calls, per 25-patch
profile. For unindexed patches its cumulative time was 4.794 ms out of 12.366 ms
in the enclosing operation on r2.02, and 6.763 ms out of 17.095 ms on r2.06.
Those approximately 39-40 percent shares describe instrumented profiles. They do
not predict an equal reduction in unprofiled request latency.

The guard walk invokes `_nested_model_schema` for ordinary dictionaries even
though peeling validator wrappers is needed only at function nodes. Simplifying
that dispatch is a measured optimization candidate that requires no stored-data
cache. Preserve nested-model and Annotated-validator rejection tests before
changing the policy. Path construction/validation is another visible cost,
especially for whole replacement; it is a separate follow-up.

These are short, warm bursts with sequential keys and aligned quantity values.
They do not characterize cold caches, random index-value distributions, long-run
throughput, unique-claim conflict rates, or application callbacks with side effects.
The baseline and follow-up timings come from different runs and must not be used
as a before/after speed claim.


### Guard dispatch optimization

The wrapper-probe simplification is implemented in `3483458be8d4043559038b318f1047bbe26445d1` and qualified
in [CI run 42279862741](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42279862741).
Both native environments passed 852 tests and all expanded workloads. Local
validation passed 443 tests; both dependency compatibility jobs passed 39 cases.
The [post-change results](benchmarks/native-guard-dispatch-2026-11-06.json) include
explicit guard call counters.

Inspection still walks the current model schema on every operation. Wrapper
recognition is restricted to function nodes and foreign class/ref descriptors;
ordinary dictionaries avoid the helper call. Existing handling of model, field,
Annotated, plain, and nested-model validators remains intact. No metadata or
validation-policy cache was introduced, and installed-schema reads are unchanged.

Each 25-patch profile still made 2,550 recursive `_has_functions` calls, while
`_nested_model_schema` probes fell from 1,000 to zero for the benchmark models.
The helper remains in use for actual validator wrappers. This establishes less
redundant work; it is not a guarantee that every workload has zero wrapper probes.

The newer r2.02 runner used an Intel Xeon Platinum 8573C instead of the prior
AMD EPYC processor. The r2.06 runner reported AMD EPYC 9V74 again. Both had four
logical CPUs. There is no controlled same-host before/after speedup claim.
Observed post-change median microseconds at 1,000 lots were:

| Engine | Workload | Patch | Whole replacement |
| --- | --- | ---: | ---: |
| r2.02 | unindexed | 156.04 | 52,422.86 |
| r2.02 | indexed | 266.74 | 250,503.03 |
| r2.02 | unique | 246.50 | 250,503.34 |
| r2.02 | reference | 364.45 | 245,786.25 |
| r2.06 | unindexed | 132.71 | 52,880.55 |
| r2.06 | indexed | 216.34 | 246,291.98 |
| r2.06 | unique | 225.48 | 244,915.19 |
| r2.06 | reference | 317.56 | 242,596.85 |

The unindexed model has two scalar fields. The tracked model adds a unique serial
and a reference, so differences between those workloads also include validation
of additional fields. Indexed patch versus replacement compares the same model.

Next optimization candidates are further reductions in guard traversal and native
path construction/validation. Any policy reuse needs explicit declaration lifetime
and invalidation semantics; stored metadata must remain checked within each TP
attempt. Unique-claim conflict-rate and longer contention measurements are also
remaining follow-ups. These results do not motivate record revisions, a storage
format migration, or a native extension as a performance shortcut.


### Child insertion results

[CI run 42312220389](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42312220389)
executed both supported engines at source `73b87d1510aa873ab5c488a2ce445e77f6cec7f4`.
The [raw results](benchmarks/native-child-insertion-2026-11-06.json) retain all sizes,
environments, p95 values, and operation counts. Median times below are microseconds.

| Engine | Collection growing | Existing lots | Direct | insert_child | Whole replacement |
| --- | --- | ---: | ---: | ---: | ---: |
| r2.02 | Target | 1 | 21.73 | 171.27 | 348.71 |
| r2.02 | Target | 999 | 21.62 | 172.70 | 58,222.03 |
| r2.02 | Unrelated | 1 | 21.35 | 180.32 | 406.18 |
| r2.02 | Unrelated | 999 | 21.43 | 185.37 | 57,111.01 |
| r2.06 | Target | 1 | 36.58 | 272.29 | 540.48 |
| r2.06 | Target | 999 | 37.06 | 277.58 | 74,546.72 |
| r2.06 | Unrelated | 1 | 36.26 | 291.95 | 621.36 |
| r2.06 | Unrelated | 999 | 36.27 | 301.77 | 74,517.79 |

At every size, target-collection insertion used 18 reads, four writes, two child
enumerations, and one transaction attempt. Inserting into a separate empty collection
used 21 reads with the same writes, enumerations, and attempts. Direct insertion used
one read and four writes. Whole replacement grew from 46 to 6,034 reads for the target
pattern and from 52 to 6,040 for the unrelated pattern, and deleted the root each time.

These serial warm samples establish bounded node work for this fixed-width layout.
They do not measure insertion contention, crash recovery, cold caches, or sustained
throughput. No fsync or journal setting is independently varied in this run. The
existing patch contention tests exercise a different operation and cannot qualify
concurrent insertion performance. Runner and engine effects are not isolated.

### Registry compilation results

The registry-owned policy and field adapters in `d19ccacfc18588d039e5c2618822f2a8ec927b2b`
passed [CI run 42312775930](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42312775930).
Both engines executed all mutation, contention, insertion, and profile workloads. The
[complete output](benchmarks/native-compiled-declarations-2026-11-06.json) records the
runtime environment and source commit. The profiles report zero `_has_functions` and
`_nested_model_schema` calls during mutations; registration performs that classification.
Field-adapter regression tests independently reject any compilation during database work.

Median patch latency in microseconds at 1,000 lots, after five warmups and over 40 samples:

| Engine | Unindexed | Equality | Unique | Reference |
| --- | ---: | ---: | ---: | ---: |
| r2.02 | 161.09 | 276.81 | 284.82 | 420.09 |
| r2.06 | 108.16 | 198.26 | 198.93 | 297.75 |

The r2.02 runner reported an AMD EPYC 7763 and the r2.06 runner an AMD EPYC 9V74. Earlier
runs used different runner assignments. These samples do not isolate the latency effect
of compilation or differences between engines. The evidence establishes eliminated
repeated declaration work and preserved integrity, rather than a fixed speedup. Setup and
registration costs are excluded; applications that recreate registries per request will
pay those costs repeatedly. Live installed-schema checks still occur on every transaction.

### Journal-enabled qualification

[CI run 42314303424](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42314303424)
executed the native suite and benchmark workloads with journaling off, then with
before-image journaling enabled, at source `f42e0c50b107c0a690d25dfefbf4256af61c6169`.
The [serial measurements](benchmarks/native-journaling-2026-11-06.json) retain environment,
mode, median/p95, and operation counts for patches, replacements, and insertion patterns.
MUPIP reported journal creation with BEFORE_IMAGES in both journal-enabled runs. Each
engine passed 1,198 tests with one expected non-native skip in each mode, plus a separate
18-case native adversarial stress pass before the journal-enabled run.

For the unindexed patch at 1,000 lots, times in microseconds were:

| Engine | Journaling | Median | p95 |
| --- | --- | ---: | ---: |
| r2.02 | Off | 158.83 | 187.10 |
| r2.02 | Before-image | 345.24 | 636.93 |
| r2.06 | Off | 163.48 | 174.93 |
| r2.06 | Before-image | 267.79 | 524.55 |

Each pair ran on the same runner in fresh databases, with the nonjournaled workload
first. Order and cache effects are not randomized, and 40 samples give coarse tails.
These measurements demonstrate the workload under both modes; they do not establish a
universal journaling multiplier, power-loss durability, recovery time, or engine-version
performance differences. Inventory benchmarks also executed in both modes. Their original
methodology string still said journaling was off; the structured journaling field and
harness mode are authoritative, and the string is corrected in subsequent output.
