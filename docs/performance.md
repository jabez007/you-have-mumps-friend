# Native mutation benchmark qualification

## Maintenance serialization workload

The disposable native harness runs python -m scripts.benchmark_maintenance when
YHMF_BENCHMARK=1. It upgrades 1/10/50 inventory warehouses from revision two to
three, each with two locations and ten lots, in committed batches of five. While
that job owns the gate, it attempts an index rebuild of an independent control model,
which has no inventory references. Refusal leaves the checkpoint unchanged; a control
name patch demonstrates unrelated CRUD.
After finishing the warehouse job through fresh handles, it completes the previously
refused rebuild and checks totals and indexes.

YHMF_MAINTENANCE_BENCHMARK records immediate refusal latency, unrelated CRUD latency,
remaining job duration, phase calls, environment, and journal mode. The API refuses
a second job rather than queuing it. Remaining job duration is a controlled scheduling
delay, excluding operator pauses; these samples do not establish actual deployment
waiting time. Native results will inform the decision in
[ADR 0009](adr/0009-model-scoped-maintenance.md). Offline tests establish workflow
correctness only. Per-model concurrent maintenance is not implemented by this benchmark.

[Native run 42394084555](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42394084555)
qualified dd3b28a36c30d96e35db579d14b8a36cbe387ed1 on both engines and journal
modes. The [raw report](benchmarks/native-maintenance-serialization-2026-11-07.json)
contains 12 controlled jobs. The second target is ^YHMFMaintControl, whose declaration
and records have no inventory references. The earlier Product probe was connected
to warehouses and is excluded from this disjoint-job evidence.

| Engine | Journal | Remaining job ms, 1 / 10 / 50 warehouses | Refusal ms | Unrelated indexed CRUD ms |
| --- | --- | ---: | ---: | ---: |
| r2.02 | Off | 58.2 / 372.1 / 1819.1 | 0.183–0.199 | 0.213–0.231 |
| r2.02 | Before-image | 64.4 / 399.9 / 1988.5 | 0.214–0.236 | 0.435–0.451 |
| r2.06 | Off | 133.6 / 843.0 / 4137.8 | 0.372–0.390 | 0.436–0.457 |
| r2.06 | Before-image | 144.5 / 896.3 / 4352.4 | 0.419–0.448 | 0.708–0.840 |

Refusal and CRUD ranges span the three different workload sizes; these are single
observations, not percentile estimates. The r2.02 host used an AMD EPYC 9V45 and
Python 3.12.12; r2.06 used an EPYC 7763 and Python 3.12.3. Both used Pydantic 2.14.3.
Host differences prevent isolating engine-version effects. Final verification and
the second completed rebuild are outside the reported remaining first-job duration.

Retain one active maintenance job. This sample reaches about 4.35 seconds with 50
small warehouses and deliberately small batches; it provides no production queue
evidence justifying a multi-job ownership/recovery protocol. It does not establish
that larger deployments never need concurrent maintenance. Reconsider from measured
operator delays and actual dependency graphs. Batch sizing and index-clear phase
calls are visible in the report and should be measured before adding new locks.

## Development-branch save differences

[CI run 42324141174](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42324141174),
commit 97439a87de2efd87996a2f69789de4fa09325154, ran the full native suite and
benchmarks on both engines with journaling off and before-image journaling on.
[Extracted replacement rows](benchmarks/native-save-differences-2026-11-06.json)
include the runner, runtime, engine, mode, and adapter operation counts.

At 1,000 unindexed lots, whole-aggregate read/change/save used **one set and no tree
deletion** per measured change. It still used 9,051 gets and 3,015 child enumerations.
The replacement path now audits physical nodes so raw extras cannot survive a save.
This retains bounded replacement semantics at additional read/traversal cost.

| Engine | Journaling | Median ms | P95 ms |
| --- | --- | ---: | ---: |
| r2.02 | Off | 96.01 | 104.00 |
| r2.02 | Before-image on | 96.24 | 104.50 |
| r2.06 | Off | 110.84 | 118.41 |
| r2.06 | Before-image on | 111.68 | 118.74 |

Each row has five warmups and 40 samples, with no concurrent writers. The counter
wrapper is included; counters measure API calls, not engine blocks or disk writes.
The mode order was fixed, and the engine versions used different runners. These
numbers demonstrate reduced writes, not a replacement-latency improvement or an
isolated estimate of journaling cost. Historical results below used earlier code
and environments. A more efficient physical traversal is a measured optimization
candidate; targeted patch remains preferable when a full aggregate is unnecessary.

## Historical full-rewrite measurements

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

## Native insertion conflict attribution

[CI 42335756704](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42335756704)
ran the [diagnostics](../tests/test_native_insertion_diagnostics.py) on r2.02/Ubuntu
22.04 and r2.06/Ubuntu 24.04, with journaling off and before-image journaling on.
The [committed observations](benchmarks/native-insertion-conflicts-2026-11-06.json)
contain all 24 experiments, operation/attempt counters, logical address evidence,
DSE block locations, and native conflict statistics. Source commit: 73704bd.

The workload uses the public insert_child operation after the fixed-width budget
improvement from issue 4. A Parent has a string key, an unindexed padding scalar,
and keyed children containing only their string identity. The region uses 4096-byte
blocks. The same declarations run with padding of zero or 16,384 bytes. Insertion
does not read that padding field; it changes where records occupy physical blocks.

For the paired intervention, two transactions pause after reading different parent
counts. The controller commits the first before releasing the second. In every
engine/journaling combination, logical read/write intersections were empty:

| Dataset | Parent R0 metadata/count block | Parent R1 metadata/count block | Attempts for two commits | Native conflicts |
| --- | --- | --- | --- | --- |
| Compact | 0x03 | 0x03 | 3 | 1 |
| Padded | 0x11 | 0x22 | 2 | 0 |

The schema fingerprint occupied a different block in each case. DSE reported the
same parent block assignments before and after the pair. No field, declaration,
or logical insertion operation changed between these datasets. This controlled
intervention supports shared data-block invalidation as the cause of the extra
restart for independent compact parents. It is stronger evidence than inferring a
block cause from attempt counts alone. The DSE TP-blkmod debug counters remained
zero; they do not supply additional attribution in this run.

Four-process bursts then performed 40 inserts per process. The following numbers
are observed attempts per commit; each burst committed and verified 160 children:

| Engine | Journaling | Separate parents: compact / padded | Shared parent: compact / padded |
| --- | --- | --- | --- |
| r2.02 | off | 2.344 / 1.000 | 3.313 / 3.250 |
| r2.02 | before-image | 1.163 / 1.000 | 2.406 / 2.469 |
| r2.06 | off | 2.331 / 1.000 | 2.294 / 2.294 |
| r2.06 | before-image | 2.131 / 1.000 | 2.319 / 2.481 |

Only the shared-parent workloads reported a logical read/write intersection: that
parent's items c:N node. Padding did not eliminate those true conflicts. Every final
count agreed with the independently enumerated child identities. Exact logical
addresses are retained for the paired interventions; burst address lists are
summarized after the overlap assertions, with full paths in the linked CI logs.

The engine defines [GVSTAT conflict counters](https://docs.yottadb.com/ProgrammersGuide/commands.html#zshow-information-codes)
and [DSE FIND block lookup](https://docs.yottadb.com/AdminOpsGuide/dse.html#qualifiers-of-find).
These experiments used read-only diagnostic commands. Their initial short header
request omitted the debug counters; the qualified run uses DUMP -FILEHEADER -ALL.

Keep the direct count safety invariant. These measurements establish a cause for
this workload, not a mapper correctness defect or a universal tuning prescription.
Artificial padding is an intervention, not an application recommendation. Fixed
case order, shared disposable region reuse, CI scheduling, and different engine/OS
hosts prevent general latency, journaling-speed, or production-throughput claims.
Measure real application block placement and transaction footprints before tuning.

The native writer contract in [issue 25](https://github.com/jabez007/you-have-mumps-friend/issues/25)
should evaluate any numeric-counter alternative explicitly; no $increment benefit
was measured here. Stored totals in [issue 17](https://github.com/jabez007/you-have-mumps-friend/issues/17)
will also introduce intentional shared writes and need their own contention evidence.
No count format or record-layout change follows from this investigation.

Reproduce only against a disposable configured native environment:

```bash
bash scripts/test-native.sh -k diagnose_native_insertion_conflicts -q
YHMF_JOURNALING=1 bash scripts/test-native.sh -k diagnose_native_insertion_conflicts -q
```

## Generated M location digest workload

The disposable native harness runs python -m scripts.benchmark_m_writers when
YHMF_BENCHMARK=1. Its YHMF_M_WRITER_BENCHMARK report contains the environment, raw
per-operation samples, medians/p95, hash calls, TP attempts/retries, final cache sizes,
and worker/parent burst duration. Latencies use M's microsecond clock around each
public child operation. Samples are buffered until the measured burst completes.
Parent duration includes release and report collection. Python control rows time
the matching public insert/patch/delete cycle and retain measured node-call totals.

The uncached control is the same generated source with only locationhash bypassed
in hwhere. Both variants retain process-local hash-call and attempt counters, with
no extra persistent writes. Setup, compilation, three warmup cycles, and coordinated
start are excluded. Cold rows clear the cache before each operation outside timing.
Churn uses 70 distinct lot paths, exceeding the 128-entry cache once field digests
are counted. Hot rows reuse one lot. Four-worker hot cases compare separate products
and lots with a shared product and separate lots, exposing reference-count sharing
as well as engine block conflicts. Final reads verify empty balances, zero totals,
indexes, outgoing edges, and the catalog's incoming counts.

This workload uses inventory warehouse revision three with root and location totals.
It measures generic balance mutations without a movement ledger or inventory service
workflow. Cold misses retain pure-M SHA-256 cost. Memoization never caches database
state and cannot eliminate native block conflicts or shared counter updates. Fixed
case order, instrumentation, small fixtures, and host differences limit general
throughput or cross-version conclusions. Offline tests establish no native latency.


### Qualified cache measurements, 2026-11-07

[Raw samples and environments](benchmarks/native-m-location-cache-2026-11-07.json)
come from [CI run 42420660451](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42420660451),
source 4965633: 132 result rows and 6,720 timed operations across both engines and
journaling modes. Each environment passed 2,516 full-suite tests with 282 skips
in each journaling mode; its separate native stress job passed 25 with four skips.
The host used Python 3.12, Pydantic 2.14.3, four logical CPUs, AMD EPYC 7763 on
r2.02/Ubuntu 22.04, and EPYC 9V74 on r2.06/Ubuntu 24.04. Engine and host changes
prevent attributing differences between environments to the engine alone.

The following one-worker hot rows each contain 20 measured calls per operation,
after three warmup cycles. Cells show median / p95 microseconds. Python uses the
same public balance mutation cycle without a movement ledger.

| Engine | Journal | Operation | M uncached | M cached | Python |
| --- | --- | --- | --- | --- | --- |
| r2.02 | off | insert | 16,021.0 / 16,084.0 | 476.0 / 538.0 | 786.1 / 822.5 |
| r2.02 | off | patch | 3,577.5 / 3,658.0 | 399.5 / 475.0 | 523.6 / 547.8 |
| r2.02 | off | delete | 15,981.5 / 16,324.0 | 427.5 / 509.0 | 557.6 / 608.7 |
| r2.02 | before-image | insert | 16,541.0 / 16,747.0 | 865.0 / 1,091.0 | 1,234.6 / 1,333.9 |
| r2.02 | before-image | patch | 3,973.0 / 4,233.0 | 688.5 / 859.0 | 871.3 / 969.6 |
| r2.02 | before-image | delete | 16,468.5 / 16,711.0 | 795.5 / 1,062.0 | 964.3 / 1,098.6 |
| r2.06 | off | insert | 13,123.0 / 13,200.0 | 331.5 / 388.0 | 603.0 / 662.8 |
| r2.06 | off | patch | 2,906.0 / 3,024.0 | 262.5 / 323.0 | 408.0 / 462.0 |
| r2.06 | off | delete | 13,240.5 / 13,295.0 | 306.5 / 339.0 | 424.9 / 499.1 |
| r2.06 | before-image | insert | 13,481.5 / 17,426.0 | 688.0 / 1,122.0 | 950.1 / 23,969.0 |
| r2.06 | before-image | patch | 3,222.5 / 47,116.0 | 524.0 / 1,248.0 | 701.3 / 831.1 |
| r2.06 | before-image | delete | 13,499.5 / 42,843.0 | 616.5 / 3,332.0 | 756.9 / 31,721.5 |

All measured hot cached M calls avoided SHA entirely. This result is conditional
on reusing paths in a live process. Clearing the cache before every operation
retained all 140 SHA calls per 20 insert/patch/delete cycles. Across these four
runs, cached versus uncached cold median ratios ranged from 0.991 to 1.010.
Cold paths therefore retain the original hashing cost. Their p95 values include
host noise; the full distributions are retained rather than summarized as a
reliable cold-path improvement.

The 70-path churn cases exceeded cache capacity. Each uncached run made 490 SHA
calls; each cached run made 142 (141 insert, zero patch, one delete). This cycle
benefits from immediate patch/delete reuse even as new inserts miss. It does not
represent an arbitrary application's hit rate. All final cache sizes stayed at
or below 128 entries, and all cases passed post-run data/index/reference checks.

Four-worker hot insert rows below contain 80 commits each. Each cell shows median
microseconds / total TP retries. Separate warehouses and lots are used in both
controls; Product identities are either separate or shared.

| Engine | Journal | Separate Product, uncached | Separate Product, cached | Shared Product, uncached | Shared Product, cached |
| --- | --- | --- | --- | --- | --- |
| r2.02 | off | 58,740.0 / 111 | 2,855.5 / 222 | 44,207.5 / 102 | 2,887.0 / 223 |
| r2.02 | before-image | 66,969.5 / 132 | 2,023.5 / 99 | 67,148.5 / 123 | 2,173.5 / 97 |
| r2.06 | off | 33,576.0 / 94 | 1,809.0 / 211 | 35,090.0 / 103 | 1,464.0 / 152 |
| r2.06 | before-image | 36,426.5 / 94 | 1,358.0 / 92 | 38,493.5 / 101 | 1,548.0 / 101 |

The cache reduced median latency in these bursts but did not eliminate retries;
retry counts sometimes increased. Substantial conflicts remain with separate
Products, so shared reverse-reference counters are not an adequate sole explanation.
These controls do not locate native blocks or assign a precise share of conflicts
to logical counters versus physical placement. No general contention reduction,
production throughput, or journaling-speed claim follows. A native hash call-out
would target cold misses and remains deferred; warm-path results do not justify
adding that dependency.
