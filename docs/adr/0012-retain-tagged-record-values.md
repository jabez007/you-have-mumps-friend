# ADR 0012: Retain tagged records and version ordered indexes separately

Status: accepted after [native CI 42334917767](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42334917767).

## Existing mechanisms and evidence

Layout one has explicit scalar tags, distinct string/integer keys, canonical
reencoding checks, and versioned schema codecs. It supports exact Decimal text,
large integers, nulls, references, and native hierarchical collections. Existing
equality index tokens preserve typed equality rather than numeric value order.
M readers can inspect every node and explicitly extract its expected payload.

The committed [observations](../benchmarks/native-value-semantics-2026-11-06.json)
come from plain M on r2.02 and r2.06, with journaling off and before-image journaling
on. All four runs passed the same 14 scalar-reader goldens. In every run:

| Operation | Observed result |
| --- | --- |
| Tagged d:12.50 plus one | 1 |
| Extracted payload or plain 12.50 plus one | 13.5 |
| Plain 0.123456789012345678901234567890 plus zero | .123456789012345678 |
| Reading that tagged Decimal payload as text | All original digits and scale |
| Arithmetic on a 40-digit integer | Truncated to 18 significant digits |
| Reading that tagged integer payload as text | All original 40 digits |
| Untagged bool 1 compared with integer 1 | Equal |
| Tagged bool compared with tagged integer | Different |
| Null n: compared with empty string s: | Different |
| Native numeric subscript order | 2, 10 |
| String-key and equality-token order | s:10 before s:2; i:10 token before i:2 |
| Writing native subscripts 2 and "2" | Same node; the second write replaced the first |

The engine documents its numeric domain in the
[language guide](https://docs.yottadb.com/ProgrammersGuide/langfeat.html#numeric-accuracy)
and [numeric considerations](https://docs.yottadb.com/MultiLangProgGuide/programmingnotes.html#numeric-considerations).
Both tagged and untagged globals can retain arbitrary decimal text. Tags do not
increase M arithmetic precision. Removing tags cannot make engine arithmetic
preserve every Python Decimal or integer value.

## Decision

Keep layout one for application records. Add no layout conversion or automatic
metadata rewrite. Explicit-tag M payload helpers make reads practical while
preserving existing data and fail-closed Python decoding. The sample helper returns
encoded text; it is not a full codec, validator, or writer contract.

Version ordered index encoding independently. A field's ordered-index contract
must declare its codec and supported domain. Native numeric subscripts can serve
values proven representable; date/time and string ordering need their own qualified
encodings. Exceeding an index's domain must fail before writes, never round claims
or silently fall back to incompatible ordering. Existing equality tokens retain
their current meaning. Ordered-index activation requires explicit schema migration
or rebuilding, qualified under issue 15; this decision adds no range-query API.

## Alternatives and tradeoffs

A schema-bound untagged layout could make ordinary M arithmetic more concise.
It would rely on the schema to distinguish bool/int and require explicit null,
precision fallback, key identity, validation, and migration rules. A mixed native/
tagged fallback adds codec branches. These are feasible designs, but there is no
measured application requirement here that justifies converting every record.

Keeping tags requires explicit payload extraction. It preserves canonical type
checks, the supported precision contract, and existing databases. Ordered index
subscripts can address query ordering and escaping overhead without changing record
values. Their key-length and precision limits still require native measurements.
There is no latency or storage-size claim in these semantic experiments.

Neither layout makes arbitrary M writes maintain indexes, unique claims, references,
counts, custom Python validation, or rollups. Issue 25 must define those writer
guarantees separately. Read-only mappings of foreign globals under issue 26 can
describe existing native values without pretending they are managed layout-one data.

## Compatibility and verification

All earlier scalar/key/schema goldens remain unchanged. New M-reader goldens include
null, bool, integer, string, Decimal, float, bytes, date, datetime, and UUID. The
reader executes on both supported engines and journaling configurations. The raw
artifact records the qualifying job version separately from M's $ZVERSION
compatibility text. No existing database requires migration for this decision.
