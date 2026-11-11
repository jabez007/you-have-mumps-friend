# Release qualification

A qualified candidate is an exact source commit and its tested artifacts. Qualification
permits downstream evaluation. It does not establish production readiness or authorize
publication. Version 0.5.0 was published to TestPyPI after
[qualification run 43052804973](https://github.com/jabez007/you-have-mumps-friend/actions/runs/43052804973).
Production PyPI publication requires a separately authorized version-tag push.
The implementation baseline is recorded in the implementation plan.

## Qualified environments

| Component | Candidate qualification |
| --- | --- |
| Python | CPython 3.12 |
| Pydantic | 2.12.0 and 2.14.3 contract/migration suites; locked version for full native tests |
| YottaDB | r2.02 on Ubuntu 22.04 x86-64; r2.06 on Ubuntu 24.04 x86-64 |
| Journaling | Off and before-image journaling on, separate disposable databases |
| Native driver | Locked YDBPython from the native extra; record its exact metadata |

The distribution accepts Python >=3.12, Pydantic >=2.12,<3 and optional YDBPython
>=2,<3. Those are dependency ranges, not evidence for every admitted version. Other
Python versions, operating systems and architectures are unqualified. MemoryStore
needs no engine. NativeStore requires an independently installed/configured YottaDB
runtime and its native dependencies. Record resolved versions with candidate evidence.

## Gates

| Level | Required evidence | Blocking |
| --- | --- | --- |
| Pull request | Ruff including C901 <=10, format, strict mypy, offline unit/property tests, executable docs, Pydantic contracts, build/install checks, routine native suite on both engines and journaling modes | Yes |
| Candidate | All PR gates on the exact commit; source and wheel metadata/content audit; clean source/wheel installation; installed native consumer, schema/job recovery, Python/M conformance and upgrade evidence; hashes and version/changelog consistency | Yes |
| Extended | Longer Hypothesis profiles, native contention stress and reproducible benchmarks with every sample and environment | Failures of correctness block; timing is informational |

Checks runs the mandatory matrix on pushes and PRs. Extended profiles and benchmark
runs execute on pushes and explicit candidate qualification. Excluding longer profiles
from PRs does not remove routine property, native, concurrency or restart tests.
Never replace native qualification with the memory adapter. Never waive an unknown
failure or silently skip an unavailable native candidate gate. Record skips and why.
Do not use a historical run to qualify changed code or different artifacts.

Power-loss recovery, replication, physical backup restoration, deployment privileges
and application authorization require operator/application qualification. Journal-enabled
tests establish transactional behavior with journaling enabled, not disaster recovery.

## Python/M conformance

Every supported generated-M write is part of the persistence contract. New operations,
field types, codecs, schema/job formats and storage changes require native evidence
on both engines and journal modes. Compare equivalent Python and M actions using
independent stores, logical reads and exact physical nodes. Audit indexes, unique
claims, owned counts, sums and reverse-reference counts/edges. Failed operations must
preserve the complete prior state. Include native errors, forced restarts, participant
rollback of earlier work and coordinated conflicting processes.

Existing qualification lives in these test groups:

| Contract | Existing tests |
| --- | --- |
| Insert, replace, delete and participants | test_m_writers, test_m_writer_transactions, test_m_reference_writers |
| Root and keyed-owner patches | test_m_root_patches, test_m_owned_writers |
| Empty roots/branches and populated deletes | test_m_root_creation, test_m_empty_owners, test_m_tree_deletes |
| Scalars, nulls, UTF-8, temporal values, choices and ordered indexes | test_m_choice_writers, test_m_temporal_writers, test_m_nullable_writers, test_m_unicode_writers, test_m_ordered_writers |
| Embedded objects and nullable presence | test_m_embedded_writers, test_m_nullable_embedded |
| Application stock plus ledger | test_m_inventory_receipts |
| Live schema, maintenance and unsupported semantics | test_m_writers, test_m_writer_maintenance and migration/concurrency suites |

These are files under tests, collected by the full native suite, not mock M tests.
Unsupported custom validators, fields or inputs must fail explicitly. Document M's
qualified textual domain separately from Python's accepted values. Raw SET/KILL,
application arithmetic and triggers outside generated routines have no conformance
guarantee. Callers must use restartable outer transactions and regenerate bound
routines after schema changes. See the M access guide for the operation/input contract.

## Candidate evidence

Retain commit, version, lockfile, exact commands, environment matrix, check conclusions,
skip counts/reasons, source/wheel hashes, package contents and isolated consumer results.
Retain historical artifact identity and upgrade limitations. Version strings in metadata
and Python must agree. No candidate is qualified when a mandatory job is missing,
cancelled, skipped or failed. Keep release notes separate from historical delivery notes.

The preparation workflow must build/test/upload artifacts only. Tags, GitHub releases
and PyPI publication are intentional maintainer actions after review. Production
publish.yml uses version-tag pushes, full candidate qualification, exact-artifact
staging and Trusted Publishing through environment pypi. Manual dispatch exercises
qualification and staging without uploading. See the release procedure for commands,
environment setup and authorization points.
