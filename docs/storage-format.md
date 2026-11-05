# Storage layout version 1

This reference defines the prototype's byte contract. The earlier architecture's
unprefixed string values and keys were illustrative. This contract distinguishes
types explicitly and never stores an entire record as an opaque blob.

## Keys and paths

Integer keys use canonical decimal ASCII, with at most 18 digits excluding the
minus sign. Integer `7` is subscript `7`. String keys use `s:` followed by UTF-8,
so string `"7"` is subscript `"s:7"`. Empty string keys become `"s:"`.
Booleans, numeric aliases such as `01`, and out-of-range integers are rejected.
Numeric keys retain native numeric collation. String keys collate as bytes.

Global names match `^[A-Za-z%][A-Za-z0-9]{0,30}` after the leading literal caret.
Subscripts are nonempty bytes without NUL. Paths have at most 31 subscripts.
The conservative encoded-key bound is the global-name byte length plus 2,
plus twice each subscript's byte length plus 2. It must not exceed 1019.
This bound may reject paths the engine could accept. Region-specific limits
can be stricter; the engine's errors propagate.

## Scalar values

| Type | Encoding | Example |
| --- | --- | --- |
| Null | `n:` | `n:` |
| Boolean | `b:0` or `b:1` | `b:0` |
| Integer | `i:` plus canonical decimal | `i:-42` |
| Float | `f:` plus Python's shortest round-trip representation | `f:1.5` |
| Decimal | `d:` plus decimal representation with exponent and trailing zeros | `d:12.50` |
| String | `s:` plus UTF-8 | `s:Main` |
| Bytes | `x:` plus standard padded Base64 | `x:AP8=` |

Nonfinite numbers and unsupported types are rejected. Node values have a
prototype limit of 32766 bytes, including the type tag. An absent node returns
`None` from the storage adapter; a persisted null is the distinct value `n:`.
Decoders reject invalid and noncanonical encodings.

The [golden scalar fixtures](../tests/fixtures/scalars.json) contain literal
encoded values for readers in other languages. Float rendering still requires
cross-language qualification before a Go writer can claim compatibility.

## Storage adapter

`Store` supplies `get`, `set`, `delete_tree`, bounded `children`, and callback
transactions with `atomic`. `children` returns immediate subscript names in
native order, strictly after the optional cursor. The limit is 1 through 10000.

`NativeStore` calls YDBPython directly. `MemoryStore` supplies rollback for offline
tests and supports the contract's integer and string collation. It does not model
other native numeric forms, native transaction restarts, or interprocess access.
