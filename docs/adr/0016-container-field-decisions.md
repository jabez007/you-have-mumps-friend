# ADR 0016: Constrained scalar lists and explicit owned containers

Status: accepted; issue 44.

Scalar lists already have bounded numbered nodes, whole-list equality indexes,
full Pydantic validation, and migration comparisons. Accept Annotated scalar,
Enum, and Literal items, including nullable items. Extract their FieldInfo and
compile constraints through the existing canonical rule representation. The
owner's existing field adapter remains responsible for validation; no second item
validator or codec is introduced. Description and presentation metadata remain
outside schema identity.

| Declaration | Decision and reason |
| --- | --- |
| list[Annotated[int, Field(ge=0)]] | Accept: scalar-list-v1 nodes and ordinary item validation suffice. |
| list[Annotated[T, ...] \| None] | Accept for supported scalar/choice T; null remains n:. |
| list[list[T]] | Reject: nested ordinal identity and mutation semantics have no demonstrated application need. Use Children[Model] with a scalar-list field. |
| list[Model] | Reject: use Children[Model] with explicit stable child keys. |
| list[Reference[T]] | Reject: use Children[Model] containing a declared reference; existing ownership and reverse entries apply. |
| dict[str, scalar] | Reject: use Children[Model] with explicit key and value fields, preserving validation and inspectable ownership. |
| int \| str and other ambiguous unions | Reject: use an explicit wrapper with a scalar discriminator and nullable declared fields. No implicit coercion or new union codec. |

The inventory application already uses Children for bins and balances. It has no
workflow requiring arbitrary maps, nested ordinal arrays, or ambiguous unions.
These decisions avoid adding representations solely for container parity. Errors
continue naming the supported declaration and its owned-model alternative.

Existing plain lists retain identical canonical snapshots and storage. Constraints
on a newly accepted item become part of its canonical items schema, so adding or
changing them requires an explicit migration. Validation-only migration checks all
existing values; failure leaves application data unchanged and can be aborted. A
named transform may correct invalid values through the existing resumable executor.
No metadata conversion occurs on open. The committed constrained-list-v1 golden
fixture covers nodes and canonical metadata on the supported Pydantic versions.

Custom item validators follow the existing custom_validation marker policy:
their Python behavior is not hashed, so changing it requires an explicit application
schema revision. They execute through ordinary validation. Arbitrary maps and new
union or nested-list storage codecs remain separate workload-driven proposals.
