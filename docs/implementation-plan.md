# Integrity hardening and hierarchical mutations

The requested milestones replace the previous v0.4 Go interoperability scope.
Go bindings and a shared-language runtime are deferred.

## Dependency order and acceptance

1. **v0.3.1 schema installation and verification.** Reuse `schemas`, `Store.atomic`,
   and bounded `children` reads. Reject unmanaged globals before installing
   metadata and verify installed snapshot identity during CRUD. Preserve layout
   version 1. Acceptance: regression failures before the fix, rollback tests,
   and native concurrent first writes.
2. **Explicit adoption.** Reuse record decoding, model validation, indexes, and
   references. Accept only bounded data already in the documented native layout;
   reject unknown nodes rather than dropping them. Acceptance: successful explicit
   adoption and failure leaving both data and metadata unchanged on both adapters.
3. **Fingerprint compatibility and detached writes.** Compare fixtures across
   compatible Pydantic versions before changing identity generation. Preserve
   legacy metadata and document an explicit upgrade strategy if needed. Demonstrate
   last-writer-wins detached saves and restart-safe atomic read/modify/write.
   Revision metadata is deferred unless an integrity defect requires it.
4. **v0.3.1 qualification.** Run Ruff (including complexity maximum 10), format,
   strict mypy, all available tests, native CI for both configured YottaDB versions,
   packaging, and installed-wheel smoke. Commit the milestone independently.
5. **v0.4 mutation design.** Inspect owned paths, record validation, index identities,
   reverse references, and migration barriers. Choose the smallest composable
   interface that validates the affected owner and any ancestor invariants without
   loading unrelated trees. Document rejected operations and addressing semantics.
6. **Mutation increments.** Scalar patch first, then keyed child insertion/removal
   and atomic composition. Reuse existing index and reference replacement with
   differences restricted to the changed owner. Keep whole-aggregate `save`.
   Acceptance includes constraints, rollback, unique/reference integrity, bounded
   node work, native restart and deletion/update races.
7. **v0.4 qualification.** Native benchmarks compare direct node updates, partial
   mutations, and whole replacements as unrelated child counts grow. Report
   measured latency and node operations, limitations, and all quality gates.

## Key risks

Pydantic validators may depend on whole ancestors; partial operations must reject
unsupported dependency shapes or explicitly load the state needed for validation.
Collection counts, root record budgets, nested index locations, and parent deletion
must remain transactional. Snapshot representation changes must not silently
reinterpret installed v0.3 metadata. In-memory tests cannot qualify native races
or native performance.
