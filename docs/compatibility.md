# Pre-1.0 compatibility

A package version identifies code, not an application's schema revision. Qualification
applies to the documented environments and exact artifacts. Pin a candidate version
and archive its hashes. An accepted dependency range does not qualify every version.

| Contract | Policy before 1.0 |
| --- | --- |
| Public Python | Documented exports, arguments and errors are supported. Patch releases preserve them except necessary correctness fixes, which require notes and regression tests. Minor releases can break APIs only with explicit release notes and an upgrade path. |
| Storage | Layout-one keys, tagged nodes, ownership, indexes and reference identities remain explicit versioned contracts. No silent reinterpretation or destructive conversion. A breaking storage change requires an explicit migration and Python/M qualification. |
| Schema metadata | Canonical representation format 2 and supported legacy format 1 keep integrity checks. Opening data does not convert metadata. Application schema revisions and custom-validator revisions are independent of representation format. |
| Maintenance | Preserve supported checkpoint/manifest formats and exact source/target/transform bindings. Resume old jobs with their original frozen declarations; do not retarget a named transform. Unknown/corrupt jobs fail closed. |
| Generated M | Documented entry points, status and input domains are supported for their bound schema. Regenerate and qualify after framework/schema upgrades. Internal labels, configuration locals and sample application helper calls are not stable public APIs. |
| Dependencies | Keep qualified versions until a documented change is justified. New admitted dependency releases require qualification; changed incidental JSON Schema must not change canonical persistence identity. |
| Internals | Modules outside documented public exports, private helpers, generated internal labels and source test utilities can change. Consumers must not import them. |

A bug fix can reject previously accepted corrupt data or unsafe inputs. State that change
in release notes; preserve valid data and provide diagnostics. No blanket promise is
made for arbitrary upgrades across all pre-1.0 minor releases. Publish tested source
and target versions with each candidate. An upgrade must either preserve existing
metadata/records or require a named, reviewed action. Never manufacture compatibility
by replacing fingerprints or bypassing schema barriers.

Before upgrading, finish active jobs using the original bindings, retain the old artifact
and generated M sources, back up, and test an actual copy. Verify schema inspection,
logical values, indexes/references and M behavior after upgrade. Reinstalling old Python
code cannot undo transformed records. Rollback can require restoring the matching
backup. See the migration cutover runbook.

The repository contains historical milestone commits but currently no published release
artifacts. Source-built historical upgrade tests are useful evidence; they must not be
reported as qualification of a released wheel that never existed. Canonical/legacy
fixtures remain regression gates, independent of downstream artifact tests.
