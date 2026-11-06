# Known limitations

This page lists behavior in version 0.5.0 that can surprise you. Each item links to its
issue in the [issue tracker](https://github.com/jabez007/you-have-mumps-friend/issues).

## Defects

| Behavior | What to do for now | Issue |
| --- | --- | --- |
| A restart caught by a broad `except` around a read inside `atomic` can still commit that attempt. | Catch only specific errors inside callbacks. | [#1](https://github.com/jabez007/you-have-mumps-friend/issues/1) |
| Bumping `__schema_version__`, or changing only a validator, constraint, or default, gives `SchemaMismatch` on open. The plan says no manual review is needed, but it cannot execute. | Do not change these on a model that holds data. Use a new global and copy the data, or restore the old declaration. | [#2](https://github.com/jabez007/you-have-mumps-friend/issues/2) |
| `extra="allow"` loses the extra values with no error, and the schema fingerprint ignores this setting. | Keep the default `extra="forbid"`. | [#3](https://github.com/jabez007/you-have-mumps-friend/issues/3) |
| `insert_child` reads the whole aggregate, so its cost and its conflict rate grow with the aggregate. | Keep shared collections small, or model high-volume children as top-level records. | [#4](https://github.com/jabez007/you-have-mumps-friend/issues/4) |
| `where` does not apply the model's configuration, such as `str_to_lower`, to filter values. | Pass values in their stored form. | [#5](https://github.com/jabez007/you-have-mumps-friend/issues/5) |
| A too-small region gives a raw `%YDB-E-REC2BIG` error on first write. | [Create the region](guides/native-setup.md#create-a-database) with larger limits. | [#6](https://github.com/jabez007/you-have-mumps-friend/issues/6) |
| `db.atomic` inside a `db.atomic` callback runs like a savepoint and is not rejected. | Do not nest. | [#7](https://github.com/jabez007/you-have-mumps-friend/issues/7) |
| A null collection on a path raises `StorageError` and not `NotFound`. | Check for null before you use the path. | [#8](https://github.com/jabez007/you-have-mumps-friend/issues/8) |
| Migrating a model that another model references requires redefining the referring model too, and `Registry(NewTarget, OldReferrer)` gives a misleading error. | Redefine the referrer against the new target. | [#9](https://github.com/jabez007/you-have-mumps-friend/issues/9) |
| A very large integer, such as `10**5000`, raises plain `ValueError`. | Catch `ValueError` as well as `YHMFError` around such values. | [#10](https://github.com/jabez007/you-have-mumps-friend/issues/10) |
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

- A migration or index rebuild blocks ordinary mapper operations on every model in the
  database until it finishes. See [stop other clients first](guides/migrations.md#stop-other-clients-first)
  and [#21](https://github.com/jabez007/you-have-mumps-friend/issues/21).
- Plain M code can read everything, but [M writes](guides/m-access.md#write-from-m) do not
  maintain indexes, references, or validation. [#25](https://github.com/jabez007/you-have-mumps-friend/issues/25)
- Index entries hold encoded value tokens, so M code walking an index does not see numeric
  values in numeric order. [#24](https://github.com/jabez007/you-have-mumps-friend/issues/24)

See also the [limits reference](reference/limits.md).
