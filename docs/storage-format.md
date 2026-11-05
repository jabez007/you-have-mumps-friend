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

## Records and installed schemas

Each record stores its declared fields under their ASCII names. Defaults are
persisted, including null and empty strings. Missing declared fields are corrupt
data; reads do not reconstruct them from defaults. The primary key is stored as
a typed scalar as well as in the path, and reads verify that they agree.

The reserved `%m` subtree contains raw ASCII metadata values for `layout`,
`schema`, `type`, and `fingerprint`. For a top-level `^Product`, `type` is `Product`.
The schema fingerprint is the lowercase hexadecimal SHA-256 of the canonical
snapshot bytes. These metadata values do not use scalar type tags.

`^YHMFSchema("Product")` holds the installed fingerprint, with the snapshot at
its `"snapshot"` child. The snapshot is compact, sorted-key, ASCII JSON containing
the layout version, global, schema version, primary-key field, and Pydantic's
validation JSON schema. Model titles and validation constraints participate in
the fingerprint. This is conservative schema compatibility, not a migration plan.
Schema installation and the first write occur in the same transaction.

Nested model schema versions, relationship kinds, and child-key fields also
participate through the snapshot's `hierarchy` entry. Scalar-only snapshots
omit that entry.

`^YHMFSchema`, `^YHMFIndex`, and `^YHMFRef` belong to the framework. The engine reserves
`^%Y*`, which the path validator rejects. A registry assigns each application
global to exactly one top-level model. Save replaces the entire record subtree;
it does not merge raw nodes or silently overwrite unmanaged records.

## Owned trees

An embedded object's field node has value `o:1`, and its declared fields are
children of that node. A null embedded object has value `n:` and no descendants.
An empty embedded object remains present through its `o:1` marker.

A child collection's field node has value `c:` plus its canonical decimal entry
count. `c:0` represents an empty collection. Each child key follows the key
encoding above. A child record's root has value `o:1`; its fields follow under
that root. Reads check the collection count, presence markers, and each child's
stored key against its physical path. The count is a consistency marker, not
a concurrency lock.

Each collection permits at most 1000 children. A whole record permits at most
10000 nodes, including record metadata and presence markers. Writers construct
and validate the full node plan before modifying storage. Readers bound every
collection traversal and reject records that exceed the node budget.

Owned models cannot declare independent globals, and ownership declarations
cannot be recursive. Child access currently goes through the loaded parent
aggregate. Updating a parent replaces its entire subtree in one transaction.

## References and reverse entries

A non-null reference is `r:` followed by the target's canonical encoded key.
The target model comes from the declaration. For `Reference[Product]`, key
`"P42"` persists as `r:s:P42`. Null references use `n:`. Both the target global
and its primary-key field participate in the source's canonical schema snapshot,
along with the fixed deletion policy `restrict`.

Reverse entries use this layout:

```text
^YHMFRef("Product","s:P42")="1"
^YHMFRef("Product","s:P42","Order",1,"<path-sha256>")="[\"lines\",\"s:L1\",\"product\"]"
```

The target root contains the number of incoming reference fields. Each leaf
identifies the source global, source root key, and relative owned-field path.
The path is a compact ASCII JSON array with `ensure_ascii` escaping. The digest
is lowercase hexadecimal SHA-256 of those exact JSON bytes. Child keys retain
their canonical key prefixes in the array. The hash keeps deep owned paths
from consuming additional index subscripts. Native key-size limits still apply
to the combined source and target identity.

Every write atomically changes the source record, reverse entries, and incoming
counts. A deletion reads the count node directly, which participates in native
transaction conflict detection. It also checks whether reverse children exist.
Any incoming reference prevents deletion. Deleting a source removes its outgoing
entries first, so a self-reference alone does not prevent deleting that source.

Unreferenced targets can retain a count of zero. Deleting the target removes its
reverse root. Missing or inconsistent entries cause errors when mapper operations
encounter them. Raw writes can bypass enforcement; this version has no automatic
repair or complete consistency scan.

## Storage adapter

`Store` supplies `get`, `set`, `delete_tree`, bounded `children`, and callback
transactions with `atomic`. `children` returns immediate subscript names in
native order, strictly after the optional cursor. The limit is 1 through 10000.

`NativeStore` calls YDBPython directly. `MemoryStore` supplies rollback for offline
tests and supports the contract's integer and string collation. It does not model
other native numeric forms, native transaction restarts, or interprocess access.
