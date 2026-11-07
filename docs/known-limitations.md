# Known limitations

This page covers the current checkout, including unreleased changes after 0.5.0.
Each item links to its
issue in the [issue tracker](https://github.com/jabez007/you-have-mumps-friend/issues).

The development branch fixes swallowed native control signals and outer-handle
transaction nesting, issues [#1](https://github.com/jabez007/you-have-mumps-friend/issues/1)
and [#7](https://github.com/jabez007/you-have-mumps-friend/issues/7). The original
v0.5.0 implementation has those defects. See the current
[transaction contract](guides/transactions.md).

The development branch also rejects lossy `extra="allow"` declarations on root
and owned models, issue [#3](https://github.com/jabez007/you-have-mumps-friend/issues/3).
Supported format-two configuration overrides retain their installed identities.

Equality predicates now apply their owning declaration's configuration and constraints,
issue [#5](https://github.com/jabez007/you-have-mumps-friend/issues/5). Decorator-based
field and model validators do not run for query predicates; `Annotated` metadata does.

Revision, constraint, nullability, and existing-default changes can now execute
validation-only migrations, issue [#2](https://github.com/jabez007/you-have-mumps-friend/issues/2).
Existing stored values must remain valid and unchanged.

Null collection paths now raise `NotFound` for owned reads and mutations, issue
[#8](https://github.com/jabez007/you-have-mumps-friend/issues/8). Invalid markers or
descendants under a null marker still raise `StorageError`.

Integers beyond Python's configured decimal conversion limit now raise `StorageError`
(also a `ValueError`), issue [#10](https://github.com/jabez007/you-have-mumps-friend/issues/10).

Native region-size errors now raise a chained `StorageError` naming `record_size`
or `key_size`, issue [#6](https://github.com/jabez007/you-have-mumps-friend/issues/6).

Child insertion counts fixed-width collections from their existing transactional
markers, issue [#4](https://github.com/jabez007/you-have-mumps-friend/issues/4).
Variable-width owned containers still require bounded traversal for node limits.

Independent parents can still share engine data blocks and restart each other's
transactions. A hot collection also shares its c:N key. The
[native conflict investigation](performance.md#native-insertion-conflict-attribution)
distinguishes these effects with logical address traces and physical block evidence;
transaction correctness does not imply contention-free execution.

`children` and `count` expose bounded owned-collection reads, and `raw_path` resolves
managed native addresses inside atomic callbacks using the existing Store and Path,
issue [#30](https://github.com/jabez007/you-have-mumps-friend/issues/30). See
[collection paging](guides/models-and-ownership.md) and
[raw addressing](guides/m-access.md#resolve-a-managed-raw-address).

Explicit `resolve`, `resolve_many`, and `get_many` provide bounded transactional target
reads, issue [#12](https://github.com/jabez007/you-have-mumps-friend/issues/12).
Indexed `occurrences` read matching owned models directly, issue
[#16](https://github.com/jabez007/you-have-mumps-friend/issues/16), with scoped occurrence
cursors and exact location validation. Existing root queries retain their interface.

`prune_migration` retires terminal job metadata and uniqueness reservations in bounded
transactions, issue [#22](https://github.com/jabez007/you-have-mumps-friend/issues/22).
Unfinished migrations still require exact Python source and target declarations.

`referrers` exposes bounded reverse-reference occurrences, and the inventory example
models supplier/product memberships as owned reference links, issue
[#14](https://github.com/jabez007/you-have-mumps-friend/issues/14).

Ordinary Python field inheritance supplies shared declarations, with the inventory's
Identified base as the example, issue
[#18](https://github.com/jabez007/you-have-mumps-friend/issues/18). It leaves storage
identities unchanged; shared-global variants remain a separate design. See
[ADR 0007](adr/0007-compile-time-model-reuse.md).

Database.path selectors infer typed owner reads without casts, and Registry.reference
infers a key-checked factory returning the existing Reference type. Tuple paths remain
available. Typed query-field objects are deferred to
[#43](https://github.com/jabez007/you-have-mumps-friend/issues/43). See
[owned selectors](guides/partial-updates.md) and [reference factories](guides/references.md).

Unique constraints apply immediately to ordinary operations. Use the explicit
patch_many group to exchange unique values in one validated transition. Callback-wide
deferred uniqueness is not supported. See [the example](guides/indexes-and-queries.md#exchange-unique-values).

## Missing features

These features remain outside the current implementation:

- Nested/model/reference lists, arbitrary `dict`, and ambiguous unions
  other than `| None`. [#44](https://github.com/jabez007/you-have-mumps-friend/issues/44)
- General computed callbacks, sums of derived sums, and reference-based dependencies.
  Root and owned Decimal sums through owned paths and virtual Pydantic fields are supported;
  see [computed fields](guides/computed-fields.md).
- Combined queries reject occurrence projections by design, because independent matches
  can have different owners.
- Mapper access rules. Apply authorization in the application's trusted service;
  independent M writers require their own enforcement. Database and raw paths do
  not provide record isolation. [ADR 0008](adr/0008-application-authorization-boundary.md)
- Automatic record transformations. Use an explicit named Transform for renames,
  removals, supported type conversions, deterministic defaults, new ownership, and
  reference changes. Root keys/globals and layouts cannot change. See the
  [transform guide](guides/record-transforms.md).
- Automatic change tracking and optimistic revisions. save validates and compares
  the full aggregate, writing only physical node differences. It remains
  last-writer-wins. [#45](https://github.com/jabez007/you-have-mumps-friend/issues/45)
  records the separate deferral decisions; [ADR 0003](adr/0003-detached-write-concurrency.md)
  gives the workload evidence and criteria for reconsidering each feature.

## Operating limits

- Registries bind references to exact target declarations. After changing a target,
  redefine referring fields against that declaration. Errors now name the source field
  and missing target. A referrer needs no migration when the target global and key identity
  are unchanged. See the [migration guide](guides/migrations.md) and
  [#9](https://github.com/jabez007/you-have-mumps-friend/issues/9).

- A migration or rebuild blocks its affected model and reference validation that
  needs that model. Unrelated CRUD can proceed; only one maintenance job runs at a
  time ([#46](https://github.com/jabez007/you-have-mumps-friend/issues/46)).
  The measured decision retains this limit; it does not add concurrent jobs. Older
  clients honoring the global gate stay blocked conservatively. See
  [maintenance guidance](guides/migrations.md#stop-other-clients-first) and
  [ADR 0009](adr/0009-model-scoped-maintenance.md).
- Raw M SET/KILL bypasses validation, indexes, references, and stored sums. Exported
  [schema-bound M routines](guides/m-access.md#export-an-explicit-m-writer) maintain
  a qualified flat ASCII str/int/Decimal and reference subset, with owning or
  caller-participating transactions. Unsupported declarations remain rejected.
  Read-only GlobalView mappings can project foreign globals without adopting them.
  [Owned trees and stored sums](https://github.com/jabez007/you-have-mumps-friend/issues/38)
  remain the next M extension. Newly generated routines validate maintenance scope;
  older exported source retains its conservative global-gate check until regenerated.
- Equality index tokens preserve typed equality, not numeric order. Opt-in ordered-v1
  indexes provide native numeric/temporal and escaped string ordering within their
  declared domains. M readers must follow the selected index codec; see
  [ordered queries](guides/indexes-and-queries.md) and
  [ADR 0012](adr/0012-retain-tagged-record-values.md).

See also the [limits reference](reference/limits.md).
