# Known limitations

This page lists behavior in version 0.5.0 that can surprise you. Each item links to its
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
issue [#5](https://github.com/jabez007/you-have-mumps-friend/issues/5). Custom field and
model validators still require a complete record and do not run for query predicates.

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

## Defects

| Behavior | What to do for now | Issue |
| --- | --- | --- |
| `insert_child` reads the whole aggregate, so its cost and its conflict rate grow with the aggregate. | Keep shared collections small, or model high-volume children as top-level records. | [#4](https://github.com/jabez007/you-have-mumps-friend/issues/4) |
| Swapping two unique values in one transaction raises `UniqueViolation`, because constraints apply after each write. | Move one value to a temporary value first. | [#23](https://github.com/jabez007/you-have-mumps-friend/issues/23) |

## Missing features

These features are not in version 0.5.0:

- Field types for `date`, `datetime`, `UUID`, `Enum`, `Literal`, `list`, `dict`, and unions
  other than `| None`. [#11](https://github.com/jabez007/you-have-mumps-friend/issues/11)
- Computed fields ([#17](https://github.com/jabez007/you-have-mumps-friend/issues/17)) and model inheritance ([#18](https://github.com/jabez007/you-have-mumps-friend/issues/18)).
- Reference traversal ([#12](https://github.com/jabez007/you-have-mumps-friend/issues/12)), `set_null` and `cascade` ([#13](https://github.com/jabez007/you-have-mumps-friend/issues/13)), and many-to-many
  and inverse relations ([#14](https://github.com/jabez007/you-have-mumps-friend/issues/14)).
- Range, prefix, sorted, and multi-predicate queries. [#15](https://github.com/jabez007/you-have-mumps-friend/issues/15)
- Queries that return owned children. Queries return whole owning roots. [#16](https://github.com/jabez007/you-have-mumps-friend/issues/16)
- Access rules. [#19](https://github.com/jabez007/you-have-mumps-friend/issues/19)
- Migrations that transform data, such as renames, removals, type changes, default
  factories, new owned containers, and reference changes. [#20](https://github.com/jabez007/you-have-mumps-friend/issues/20)
- Pruning of completed migration jobs. Their reservations keep normalized unique values. [#22](https://github.com/jabez007/you-have-mumps-friend/issues/22)
- Change tracking. `save` always rewrites the whole aggregate. [#27](https://github.com/jabez007/you-have-mumps-friend/issues/27)

## Operating limits

- Registries bind references to exact target declarations. After changing a target,
  redefine referring fields against that declaration. Errors now name the source field
  and missing target. A referrer needs no migration when the target global and key identity
  are unchanged. See the [migration guide](guides/migrations.md) and
  [#9](https://github.com/jabez007/you-have-mumps-friend/issues/9).

- A migration or index rebuild blocks ordinary mapper operations on every model in the
  database until it finishes. See [stop other clients first](guides/migrations.md#stop-other-clients-first)
  and [#21](https://github.com/jabez007/you-have-mumps-friend/issues/21).
- Plain M code can read everything, but [M writes](guides/m-access.md#write-from-m) do not
  maintain indexes, references, or validation. [#25](https://github.com/jabez007/you-have-mumps-friend/issues/25)
- Index entries hold encoded value tokens, so M code walking an index does not see numeric
  values in numeric order. [#24](https://github.com/jabez007/you-have-mumps-friend/issues/24)

See also the [limits reference](reference/limits.md).
