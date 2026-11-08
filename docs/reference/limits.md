# Limits

## Mapper limits

| Limit | Value | Defined in |
| --- | --- | --- |
| Children in one collection | 1000 | `MAX_CHILDREN`, `records.py` |
| Scalars in one list | 1000, also subject to the root node budget | `MAX_ITEMS`, `scalar_lists.py` |
| Stored nodes in one record, including metadata and presence markers | 10,000 | `MAX_RECORD_NODES`, `records.py` |
| Records in one query page | 1 to 1000, default 100 | `MAX_QUERY_RECORDS`, `queries.py` |
| Combined candidate/value/occurrence/probe visits in one ordered or combined query | 1000 | `MAX_VISITS`, `index_scans.py` |
| Predicates in one query | 8 | `MAX_PREDICATES`, `query_intersections.py` |
| Items in `get_many` or `resolve_many` | 0 to 1000, including duplicates | `MAX_QUERY_RECORDS`, `queries.py` |
| Owners in one `patch_many` call | 1 to 100, no duplicate owner addresses | `snapshot`, `grouped_mutations.py` |
| Distinct decoded owners in one policy delete | 100, including its initial target | `MAX_OWNERS`, `deletions.py` |
| Incoming edges across one deletion closure | 1000 | `MAX_EDGES`, `deletions.py` |
| Distinct decoded value nodes in one policy delete | 10,000 | `MAX_NODES`, `deletions.py` |
| Parents in one migration batch | 1 to 100 | `MAX_BATCH_SIZE`, `migration_executor.py` |
| Leaves removed in one job-pruning batch | 1 to 100, default 25 | `prune`, `migration_jobs.py` |
| Records in single-transaction `adopt()` | 100, also subject to its total node budget | `MAX_ADOPTION_RECORDS`, `adoption.py` |
| Records in one resumable adoption batch | 1 to 100, default 25; the job can span more than 100 records | `advance`, `adoption_jobs.py` |
| Bytes in one node value, including the type tag | 32,766 | `MAX_VALUE_BYTES`, `storage.py` |
| Digits in an integer key | 18, excluding the sign | `MAX_INTEGER_KEY`, `codecs.py` |

Source files are in `src/mccann_hub/yhmf/`.

## Stored sums

The decimal-sum-v1 contract in `rollups.py` accepts finite Decimal source values
with at most 18 significant digits, at most 18 fractional digits, and magnitude
below 1E18. It adds exact integer units of 1E-18, with at most 40 digits in the
aggregate unit count. Target field constraints and index limits also apply.
Values outside this domain fail rather than round; the active Decimal context
does not change the result. See [stored sums](../guides/computed-fields.md).

## Paths and names

These limits come from the [storage contract](../storage-format.md#keys-and-paths):

- Global names match `^[A-Za-z%][A-Za-z0-9]{0,30}` after the leading caret.
- Subscripts are nonempty and contain no NUL byte.
- A path has at most 31 subscripts.
- A conservative encoded-key bound must not exceed 1019.

## Region settings

[`scripts/test-native.sh`](../../scripts/test-native.sh) creates its region with
`-key_size=1019 -record_size=32767`. The native test suite is qualified only with these
settings, plus a default-region diagnostic regression. Smaller regions apply stricter
limits. `REC2BIG`, `KEY2BIG`, and `GVSUBOFLOW` become `StorageError` naming the setting;
the original driver exception remains as the cause. Other engine errors propagate.
Record limits cover combined key and value bytes, not just the value.

## Index values

Index subscripts encode values as JSON with non-ASCII escaped, so a character can take
much more than one byte. Values are never truncated or hashed to fit. An oversized value
raises an error before any record write.

A live r2.06 test used a region with the settings above, model `^Idx`, and field `s`.
The longest equality-indexed strings were:

| Characters | Longest value |
| --- | --- |
| ASCII | 423 |
| CJK | 70 |
| Emoji | 35 |

Longer model and field names reduce these numbers.

## Supported versions

| Component | Version | Source |
| --- | --- | --- |
| Python | 3.12 or later, qualified on 3.12 | `pyproject.toml` |
| Pydantic | 2.12 or later, below 3 | `pyproject.toml` |
| YottaDB driver | `yottadb` 2.0 or later, below 3 | `pyproject.toml` |
| YottaDB | r2.02 on Ubuntu 22.04, r2.06 on Ubuntu 24.04 | `.github/workflows/checks.yml` |
