# Independent pre-release evaluation

This report records qualification of an unpublished 0.5.0 candidate. It does not
announce publication or production readiness. The report-only commit can follow the
qualified source commit without changing those archived artifacts.

## Baseline and candidate

Baseline cf3d826fe8fec5f6810943c072a5cdead18af713 declared version 0.5.0, with no
Git tags or GitHub releases. Only the pre-existing .serena directory was untracked.
Baseline CI 42510441710 passed. The PyPI project endpoint returned 404 on 2026-11-08;
no package-name reservation is implied.

Selected candidate source: 2579e6a627aa78fabe9284082f2bd88868a29b4e.
The final manual qualification is
[run 42532447268](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42532447268).
All jobs passed, including the final evidence job. The downloaded manifest records
qualification_status=passed and source_tree_clean=true.
No version bump, release tag, GitHub release, PyPI publisher or upload was created.

## Qualified artifacts and native results

The [qualified-candidate download](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42532447268/artifacts/13638392250)
contains the exact tested artifacts, SHA256SUMS and qualification.json. The downloaded
copies are in dist/qualified-2579 in the working checkout, outside version control.
Both sha256sum and a repeated metadata/content audit passed after download. Preserve
these files before the Actions retention period expires.

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| mccann_hub_yhmf-0.5.0-py3-none-any.whl | 150184 | 09d4035b1f4e931bd43de49c0a4bec4374a80427fc91c76daef031420c0c3bc2 |
| mccann_hub_yhmf-0.5.0.tar.gz | 729315 | 95e4496c3ebe926b65e2594991a697467bf59cab7f271a4813d93316c11abf76 |

| Qualified configuration | Full suite | Separate native stress |
| --- | --- | --- |
| r2.02, Ubuntu 22.04 x86-64, journaling off | 2,766 passed, 456 skipped | 25 passed, four skipped |
| r2.02, Ubuntu 22.04 x86-64, before-image journaling | 2,766 passed, 456 skipped | Same separate engine stress job |
| r2.06, Ubuntu 24.04 x86-64, journaling off | 2,766 passed, 456 skipped | 25 passed, four skipped |
| r2.06, Ubuntu 24.04 x86-64, before-image journaling | 2,766 passed, 456 skipped | Same separate engine stress job |

The full suites include memory and native fixtures, not 2,766 native-only cases.
Intentional skips include memory fixtures unable to execute M and operation/layout
selection cases. No mandatory job was skipped. Both Pydantic boundary jobs passed
839 selected tests. Offline stress passed 21 tests. Both artifacts installed and ran
native migration/rebuild/mutation smoke in every configuration. Both independent fresh
and historical-upgrade cases passed in actual separate disposable databases, including
M execution. Benchmarks and their final consistency assertions completed as well.
Native CI used YDBPython 2.0.1, Pydantic 2.14.3 and CPython 3.12.12 on Ubuntu 22.04,
3.12.3 on Ubuntu 24.04. The base build used 3.12.3 and uv 0.9.5. Other admitted
runtime versions are not thereby qualified.

The exact native commands were the workflow's ydb_dist=/opt/yottadb/test,
YHMF_ARTIFACT_DIR=dist/candidate and YHMF_CONSUMER=1 with:

```sh
ydb_dist=/opt/yottadb/test YHMF_ARTIFACT_DIR=dist/candidate \
  YHMF_CONSUMER=1 YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
ydb_dist=/opt/yottadb/test YHMF_HYPOTHESIS_PROFILE=stress PYTHONHASHSEED=0 \
  bash scripts/test-native.sh -m 'adversarial and native' -q --hypothesis-show-statistics
ydb_dist=/opt/yottadb/test YHMF_ARTIFACT_DIR=dist/candidate YHMF_CONSUMER=1 \
  YHMF_JOURNALING=1 YHMF_BENCHMARK=1 bash scripts/test-native.sh -q
```

Those variables are recorded per step in the workflow. The stress step does not set
YHMF_ARTIFACT_DIR/YHMF_CONSUMER and does not repeat installed consumer qualification.
The manual version check, hashes, clean source identity and evidence promotion all
passed on the selected commit. Preparation produced Actions artifacts only.

## Changes by phase

1. Added qualification and pre-1.0 compatibility policies. Kept the existing native,
   concurrency, migration and Python/M suites mandatory; moved longer stress and
   benchmark work out of PRs. The full matrix is reusable by manual candidate runs.
2. Added artifact content/metadata checks, fresh source/wheel installations, native
   installed-package smoke, hashes and provenance. Fixed a confirmed sdist defect:
   untracked local .serena settings/caches were included. The source whitelist excludes
   developer state. Exact GPL license, M templates, typed namespace and dependencies
   are checked. Candidate preparation has read-only repository permissions and no
   publication/tagging steps. Pending build identity becomes passed evidence only
   after the mandatory reusable qualification succeeds.
3. Added a separately packaged inventory consumer, installed outside the checkout.
   It pins the selected package and uses only documented public imports. Strict mypy
   includes it. Fresh/upgrade cases exercise stock, history, indexes, rollback and
   native M. A failing future-version regression also removed the historical builder's
   hard-coded current pin.
4. Added installation/operations guidance and a cutover runbook. A failing regression
   proved the legacy Python inventory service still wrote after move initialization.
   Current Python/M legacy service entries now refuse checkpoint presence. This is
   application-level cooperation, not fencing of old binaries, raw writers or generic
   mapper calls. No distributed lock or storage/API revision was introduced.

## Executed local validation

Local environment: CPython 3.12.11, uv 0.9.5, locked Pydantic 2.14.3.
All commands below were executed. The ordinary native suite was not executed locally:
the available engine is unpacked, with a user-owned 755 helper rather than the
required privileged installation. Native results come from the actual CI matrix.

| Command | Observed result |
| --- | --- |
| .venv/bin/ruff check . | Passed, including C901 <=10 |
| .venv/bin/ruff format --check . | Passed |
| .venv/bin/mypy | Passed, 65 source files |
| .venv/bin/pytest -m 'not native' -q --tb=short | 1,269 passed, 1,953 deselected, 94.60 seconds |
| .venv/bin/python -m scripts.check_doc_examples | 61 Python blocks passed; document count can increase with this report |
| bash -n scripts/test-native.sh | Passed |
| UV_CACHE_DIR=/tmp/yhmf-uv-cache uv build --offline --out-dir /tmp/yhmf-candidate-2579-a | Wheel and source built |
| .venv/bin/python scripts/check_artifacts.py /tmp/yhmf-candidate-2579-a --expected-version 0.5.0 | Passed; exact source commit and clean packaged inputs |
| UV_CACHE_DIR=/tmp/yhmf-uv-cache .venv/bin/python scripts/qualify_install.py dist/qualified-2579/mccann_hub_yhmf-0.5.0-py3-none-any.whl --offline | Fresh isolated install/smoke and dependency check passed |
| UV_CACHE_DIR=/tmp/yhmf-uv-cache .venv/bin/python scripts/qualify_install.py dist/qualified-2579/mccann_hub_yhmf-0.5.0.tar.gz --offline | Fresh isolated source build/install/smoke and dependency check passed |
| UV_CACHE_DIR=/tmp/yhmf-uv-cache .venv/bin/python scripts/qualify_consumer.py dist/qualified-2579/mccann_hub_yhmf-0.5.0-py3-none-any.whl --offline | Fresh independent app passed |
| UV_CACHE_DIR=/tmp/yhmf-uv-cache .venv/bin/python scripts/qualify_consumer.py dist/qualified-2579/mccann_hub_yhmf-0.5.0-py3-none-any.whl --mode upgrade --offline | Historical dependency upgrade, explicit migration and workflows passed |

The final downloaded files above were reinstalled/re-exercised locally after their
successful native CI qualification. Separate candidate-2579-a and candidate-2579-b
local builds produced identical wheel and source bytes when both used SOURCE_DATE_EPOCH
from git log -1 --format=%ct. Those experiments used CPython 3.12.11 and are not claimed
to be byte-identical to the CI build, which used a different timestamp/environment.
This is repeatability under a tested build environment, not every backend/host pair.

A fresh standard venv/pip installation also passed the isolated smoke and pip check.
It installed from the downloaded qualified wheel with PIP_INDEX_URL=https://pypi.org/simple and
PIP_CONFIG_FILE=/dev/null; no editable or repository import was allowed. Separate
UV_CONSTRAINT runs using a file containing pydantic==2.12.0 passed both consumer modes
against candidate-2579-a. Their install logs confirmed Pydantic 2.12.0/core 2.41.1;
the locked cases used 2.14.3/core 2.50.4. These lower-bound local runs used MemoryStore,
not native YottaDB.

The negative source-artifact regression failed on the original sdist before the
whitelist fix. The legacy write guard failed before its fix. The 0.6.0rc1 pin fixture
failed before its fix. All now pass without relaxed complexity or assertion thresholds.

For the downloaded standard-pip check, an isolated venv at
/tmp/yhmf-qualified-standard-pip installed the explicit qualified wheel, ran a copied
smoke.py with python -I from /tmp, and passed python -m pip check. Source/wheel
qualify_install and both qualify_consumer commands above were repeated on the exact
CI downloads, rather than assuming locally rebuilt bytes matched.

## Independent application and compatibility

The self-contained project is consumers/inventory-ledger. Workspace write permissions
allow this repository and temporary directories, so the repeatable fixture copies the
project outside the source tree, builds its own wheel and installs it into a fresh
venv beside the explicit YHMF artifact. Runtime imports are limited to the documented
root package and public errors; a regression enforces that rule. The application is
also runnable independently with ordinary pip and isolated Python.

Its own models define independent products, lots, warehouses and locations, with
balances owned under each location. It receives 20, transfers seven and adjusts by
-0.5, expecting 13 and 6.5. It checks product/location/lot retrieval, bounded history,
query planning, duplicate-ID and overspend rollback, reference restrictions and indexes.
Native evaluation exports a Product writer from the installed package, executes M,
and checks Python indexed reads plus exact tagged native bytes.

The historical path builds 0.4.0 at immutable commit
51e8456ae6762ac2ebc8db1a74b914c2cc3c280c and a legacy consumer pinning it. The old
runtime creates actual metadata/data. After installing the candidate, reopening the
old declaration must leave bytes unchanged. An explicit additive Location revision
then runs through the public migration planner/job API before application operations.
Native cases reopen the actual same database; offline cases exchange public Store
nodes as fixtures. There are no published historical artifacts, so this does not
qualify arbitrary released 0.3/0.4 installations. Existing schema/job goldens remain
mandatory independent regression evidence.

No private API or new framework convenience feature was needed. Dynamic tuple paths
need a typing cast; current typed selectors support reads. Indexed queries return
matching root aggregates, not projected matching children. The app documents page
units and single-filter behavior. Existing storage, schema, migration and public Python
contracts are unchanged. Generated M entry/status contracts remain qualified; private
arithmetic/configuration labels remain internal and app source must be regenerated.

## Operations, blockers and next steps

No known technical blocker remains for evaluation in this exact qualified matrix.
Publication, production deployment and untested upgrade paths remain separate decisions.

The operations guide and cutover runbook separate engine installation, journal durability,
backups and process control from mapper invariants. writers_stopped=True is an assertion.
Current service guards do not prove external/old writers stopped. Schema job barriers
and the inventory copy/clear have different scopes and abort boundaries. Committed
transformation can require resume or backup restoration, not a package downgrade.

Existing measured performance reports remain the evidence for ownership budgets,
partial/whole operations, index cost, shared sums/reference counts, native block
conflicts and bounded queries. Current CI also executes the reproducible benchmarks.
There is no new universal performance or production-throughput claim.

Publication remains a separate intentional action: choose final version/release notes,
review artifact/licensing/deployment terms, qualify that exact final version, and
explicitly authorize tags/releases/uploads. Configure any future PyPI publisher through
a protected Trusted Publishing environment. No publishing credentials or dormant
publishing permissions were added here.

Priorities after independent evaluation:

1. Exercise an operator-owned backup/restore and journal-recovery drill on realistic
   data before production use. Power-loss, replication and failover remain untested.
2. Archive external-user feedback and a real application's writer inventory/cutover
   evidence. Do not treat the example's cooperative checkpoint as universal fencing.
3. Extend the runtime/upgrade matrix only from concrete adoption demand. Other Python
   versions, platforms and historical artifact paths remain unqualified. Preserve
   failing reproductions instead of adding broad ORM features.
