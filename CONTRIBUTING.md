# Contributing

See [release qualification](docs/release-qualification.md) for blocking gates,
[candidate preparation](docs/releasing.md) for isolated artifact/native checks, and
[the independent consumer](consumers/inventory-ledger/README.md) for downstream tests.
PRs keep routine native/concurrency/conformance tests. Longer stress profiles and
benchmark timings run on pushes and explicit candidate qualification.

## Run the routine checks

Install [uv](https://docs.astral.sh/uv/), then run these commands:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
```

Ruff limits cyclomatic complexity to 10. Native tests skip unless you select a
disposable database. The in-memory adapter does not prove native conflict handling
or process concurrency.

## Run the native storage tests

1. Install [YottaDB](https://docs.yottadb.com/AdminOpsGuide/installydb.html).
   An unpacked binary archive is not enough, because YottaDB needs a correctly
   installed `gtmsecshr` helper.
2. Set `ydb_dist` to the installation directory.
3. Run the suite:

   ```sh
   uv sync --extra native
   bash scripts/test-native.sh -q
   ```

The script creates and removes its own temporary database. Setting
`YHMF_TEST_NATIVE=1` by hand runs the tests against your current database, so use
it only with disposable data.

Native CI runs this suite with Python 3.12 on YottaDB r2.02 with Ubuntu 22.04 and on
r2.06 with Ubuntu 24.04. See
[`.github/workflows/checks.yml`](.github/workflows/checks.yml).

Native qualification covers the following behavior:

- Forced transaction restarts and rollback of schema installation.
- Four independent processes committing 160 updates to one record, while concurrent
  reads assert that both fields agree.
- Reference races. Processes pause after reading the same incoming count, then
  release together. The tests check reference creation against target deletion,
  concurrent addition and removal, and index state after native restarts.
- Unique-claim races. Two processes read the same unclaimed value. Exactly one source
  and its reverse-reference updates must commit.

These tests establish correctness for the tested environments. They do not establish
throughput or compatibility with other engine releases.

[Correctness testing](docs/testing.md) covers generated state machines, independent
integrity assertions, fault injection, longer profiles, and reproduction commands.

## Benchmark native mutations

```sh
YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
```

After the tests pass, this command runs `scripts/benchmark-mutations.py` in the same
disposable database. Both native CI environments run it too.

The output has three parts:

- **Serial measurements.** These report median and p95 latency and adapter operation
  counts at 1, 10, 100, and 1,000 lots, with five warmups. The unindexed case compares
  direct node assignment, deep patch, and read-modify-save. Indexed quantity, unique
  serial, and indexed reference changes compare patch and replacement under the same
  integrity requirements.
- **Four-process contention bursts.** These compare independent roots, independent
  children, and a shared child. Callback-attempt counts show retry work. Each workload
  ends by checking final values, indexes, and reverse-reference counts.
- **cProfile summaries.** Profiles run without the operation-counting wrapper and are
  separate from the latency samples.

Direct assignment measures a `NativeStore` transaction and one scalar write. Latency
includes Python and counting overhead. Serial measurements use a warm, uncontended
database. Contention throughput includes controller coordination. These numbers
describe the measured workloads and do not establish production throughput. Offline
operation counts establish work scope only.

See the [performance report](docs/performance.md) for native evidence and limits.
