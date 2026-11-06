# Correctness testing

Run routine checks with the locked development environment:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m 'not native'
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
