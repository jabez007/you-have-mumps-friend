# Correctness testing

Run routine checks with the locked development environment:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m 'not native'
uv run python -m scripts.check_doc_examples
```

Ruff includes C901 with a maximum of ten. Existing codec properties retain their
normal Hypothesis settings. New nested/codec/schema properties use 100 examples;
the routine mutation state machine permits 20 examples of up to 30 steps on the
memory adapter, and eight examples on native YottaDB. These are bounds, not a
claim that Hypothesis exercises every combination.

## Generated sequences and independent assertions

The mutation state machine owns ordinary dictionaries describing warehouses,
bins, children, unique values, and product identities. Its transition decisions
do not use mapper validation, query planning, or persistence helpers. Operations
include inserts, replacements, scalar/index/reference changes, child insertion
and deletion, restricted product deletion, invalid mutations, unique conflicts,
and explicit transaction aborts.

After every step, public reads must equal the reference state. A separate audit
derives complete expected secondary indexes, unique claims, reverse edges, and
counts from plain values and the documented storage format. It does not call
mapper codecs, entry builders, or the framework's index audit. Exact comparison
also detects extraneous/stale entries. Failed writes and aborts additionally
compare full physical snapshots where appropriate.

Boundary properties cover Unicode, bytes, Decimal scale, numeric/string identity,
null versus empty versus missing, malformed encodings, key sizes, depth limits,
node sizes, deterministic schemas, and persistence round trips. Unpaired surrogate
strings have deterministic regressions: key and scalar encoding raise a value-free
`StorageError` instead of leaking a Python encoding exception.

## Longer profiles and reproduction

```sh
YHMF_HYPOTHESIS_PROFILE=stress uv run pytest \
  -m 'adversarial and not native' --hypothesis-show-statistics
```

The separate adversarial CI job permits 150 memory state-machine examples of up
to 100 steps. Native stress permits 30 examples of up to 100 steps. Default
routine limits remain small. Deadlines are disabled for these functional tests;
performance is measured separately. Rule arguments and minimized sequences appear
in failures. Generated property failures enable Hypothesis reproduction blobs.
Keep that output and the installed Hypothesis version. Re-run a reported case or
use `--hypothesis-seed=NUMBER`; `.hypothesis/examples` also helps local replay.

## Native behavior and migration interruption

Use an installed YottaDB distribution with its required privileged helper:

```sh
ydb_dist=/opt/yottadb/test uv sync --locked --extra native
ydb_dist=/opt/yottadb/test bash scripts/test-native.sh -q
YHMF_HYPOTHESIS_PROFILE=stress ydb_dist=/opt/yottadb/test \
  bash scripts/test-native.sh -m 'adversarial and native' -q
```

The script creates and removes a disposable database. Native CI qualifies r2.02
on Ubuntu 22.04 and r2.06 on Ubuntu 24.04. The memory adapter does not establish
native conflict/restart behavior. Do not direct these tests at application data.

CI also runs the full suite and benchmarks in a second disposable database with
`YHMF_JOURNALING=1`. The harness enables before-image journaling with MUPIP before
opening the driver; benchmark output records the mode. To reproduce:

```sh
YHMF_JOURNALING=1 YHMF_BENCHMARK=1 ydb_dist=/opt/yottadb/test \
  bash scripts/test-native.sh -q
```

This exercises journal-enabled CRUD, migrations, rollbacks, and coordinated process
tests. It does not test power loss, journal recovery, backup restoration, or
replication. The default-region diagnostic test uses a separate default-sized region.
For journal administration, see the
[upstream guide](https://docs.yottadb.com/AdminOpsGuide/ydbjournal.html).

Existing coordinated workers cover independent children/fields, unique contention,
parent deletion, references, concurrent initialization, and forced restarts. New
workers warm a legacy schema cache, pause inside an active transaction, then observe
compatible, incompatible, or corrupted metadata committed by another process.
Only compatible changes permit the mutation to commit. Rendezvous uses explicit
process communication rather than sleeps.

Installation fault injection interrupts writes of either manifest, the checkpoint,
the gate, and the fingerprint barrier. Every failure must restore all namespaces.
Recovery tests reopen after committed progress in each migration phase and verify
completion and idempotent completed-job calls. Existing tests additionally inject
failures during field backfills, index/unique maintenance, checkpoints, retagging,
cleanup, and activation. These tests complement, rather than replace, actual native
transaction and process tests.

## Executable documentation

The documentation checker executes every exact `python` fence in README, docs, and
example READMEs, in document order. Each document gets a fresh Python namespace, and
tracebacks retain its Markdown line numbers. Run it as a module from the repository
root. Architecture sketches use `python illustrative` fences because they require
application context or describe future work; they are not executed. Shell and M
snippets are outside this Python checker and require their native harnesses.

The offline test suite runs these same examples and verifies that README links are
absolute or local anchors. GitHub file links are checked against local files, so the
PyPI long description does not depend on repository-relative link resolution. This
does not validate external website availability or qualify a package publication.

The journal-enabled addition passed both supported engines in
[CI run 42314303424](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42314303424),
source `f42e0c50b107c0a690d25dfefbf4256af61c6169`: 1,198 tests per engine per journal
mode, plus the separate native stress pass. The same run executed 29 Python
documentation blocks and passed both Pydantic compatibility jobs. Subsequent examples
and tests can increase these counts. See the
[journal measurements](performance.md#journal-enabled-qualification).
