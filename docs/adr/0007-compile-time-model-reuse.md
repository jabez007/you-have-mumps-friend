# ADR 0007: Reuse fields at declaration time

Status: accepted for the inventory example.
[CI run 42320796509](https://github.com/jabez007/you-have-mumps-friend/actions/runs/42320796509)
passed both configured native engines before
[issue 18](https://github.com/jabez007/you-have-mumps-friend/issues/18) was closed.

## Existing mechanisms and problem

Pydantic already inherits fields and configuration through ordinary Python classes.
The registry compiles the resulting model_fields; it does not persist Python class
names or MRO details. Inventory repeats the same nonempty string primary key across
catalog, hierarchy, ledger, and supplier models. Existing embedded objects and
References already express owned and independent shared data.

## Decision

Use an unregistered Identified base containing the common id field. Concrete roots
declare their own global; owned models inherit the same field without a global.
This is declaration reuse and introduces no inheritance-specific persistence API.
The registry continues to require exactly one key and one model per application global.

Extending an existing stored model uses an explicit replacement declaration and the
normal reviewed migration. An independent model declares an independent global.
Inherited fields use their ordinary field names, index paths, and reference identities.
Do not mutate registered classes to install extensions at runtime.

## Alternatives and tradeoffs

Delegation through embedded objects is appropriate when the shared part belongs to
its parent. A Reference is appropriate when it has an independent lifecycle. Both
remain available; neither is needed for repeating an id declaration.

Variants sharing one global would need a discriminator, variant registration,
subtype validation and query rules, and a storage migration. Inventory currently
has distinct catalog, supplier, warehouse, and ledger identities. Their shared key
declaration does not demonstrate a requirement for polymorphic shared storage.
Defer that design until a concrete workflow needs it.

The application remains responsible for inherited Python behavior and configuration.
Custom validator changes still require application schema revisions. Python inheritance
alone does not create stored relationships between base and derived records.

## Compatibility and evidence

The committed pre-refactor inventory schema fixture records all five current root
contracts. They must remain byte-identical after field reuse, under both qualified
Pydantic versions. A legacy Product snapshot must remain readable without metadata
conversion. Integration tests exercise inherited keys, indexes, nested ownership,
and an additive inherited-field migration. Existing migration and inventory tests
remain required. No field names, codec versions, layouts, or fingerprints change.
