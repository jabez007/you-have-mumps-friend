# Virtual fields and stored sums

Use a Pydantic computed_field or ordinary property for a value derived from a loaded
object. It is never stored or indexed and does not fetch missing data. Use a stored
sum when a root needs an indexed total maintained with its owned quantities.

```python
from decimal import Decimal, localcontext
from pydantic import computed_field
from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry


class Balance(Model):
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(ge=0, max_digits=18, decimal_places=6)


class Warehouse(Model):
    __global__ = "^ComputedWarehouse"
    id: str = Field(primary_key=True)
    balances: Children[Balance] = ChildField(key="id")
    on_hand: Decimal = Field(sum_of="balances.quantity", index=True, ge=0)

    @computed_field
    @property
    def loaded_total(self) -> Decimal:
        with localcontext() as context:
            context.prec = 40
            return sum((item.quantity for item in self.balances.values()), Decimal(0))


db = Database(MemoryStore(), Registry(Warehouse))
value = Warehouse(id="W", balances={"L": Balance(id="L", quantity=Decimal("2.50"))})
assert value.on_hand == 0 and value.loaded_total == Decimal("2.50")
persisted = db.insert(value)
assert persisted.on_hand == persisted.loaded_total == Decimal("2.50")
db.patch(Warehouse, "W", at=("balances", "L"), changes={"quantity": Decimal("3.75")})
assert db.get(Warehouse, "W").on_hand == Decimal("3.75")
assert db.query(Warehouse).where(on_hand=Decimal("3.75")).all()[0].id == "W"
```

sum_of follows dotted declarations through owned objects and child collections,
without keys. Each occurrence of the source field contributes once. Empty and null
containers contribute zero. The initial implementation requires a root Decimal
target and a nonnullable Decimal source below at least one child collection. A
literal Decimal zero default is implicit. Targets can use normal equality, unique,
or compatible ordered indexes. The canonical snapshot records the path and the
versioned decimal-sum-v1 contract; ordinary scalar storage remains unchanged.

Model instances remain value objects. A freshly constructed object's stored field
starts at its default; insert/save returns the validated value with its derived
sum. Changing a loaded child does not automatically change that object's stored
snapshot or the database. Use patch/child operations for persistence, or intentionally
replace the aggregate with save and use its returned value. A virtual property can
reflect the current loaded children. Neither mechanism adds change tracking.

## Validation and numeric domain

Whole writes run normal aggregate validation, compute from its final values, then
validate the sum's target field adapter. Partial mutations run the existing owner
validation, including leaf model validators. They include any validated quantity
changes in their delta, even when a different field was patched. Custom validation
on root/ancestor owners cannot safely run without loading those aggregates, so
these declarations are rejected. Source owners with descendants require local
validation. There is no silent validator bypass or user callback used for the sum.

Source values must be finite Decimals with at most 18 significant and 18 fractional
digits and magnitude below 1E18. Trailing zero scale and signed zero do not change
contributions. Computation uses exact integer units of 1E-18, independent of Decimal
context; totals are bounded to 40 unit digits by the record domain. Unsupported
values fail before writes. Declare application constraints such as the example's
max_digits/decimal_places to reject narrower business limits during construction.
A total's own constraints and selected index domain also apply; their failure rolls
back the source mutation. No value is rounded to fit.

Direct patches of a stored sum are rejected. Whole stored-owner reads verify it
against loaded inputs; corrupt or missing totals fail safely. Incremental writes
trust the existing managed total after validating its encoding/domain. They do not
rescan siblings to detect arbitrary prior raw changes. Repairing unsupported raw
writes requires an explicit operator procedure, not a read-time correction.

## Transactions, efficiency, and indexes

A partial quantity update reads/writes the affected owner and one total per surviving
root. It never loads unrelated sibling quantities. Patch groups combine all deltas
for a root once; a net-zero group leaves that total and its index untouched. Child
insertion/deletion, cascading child deletion, and set_null validators contribute
through the same secondary transition. Deleted whole roots need no surviving total.
Normal index/unique-claim maintenance is reused in the transaction; unrelated indexes
are untouched. Use patch_many for related existing-owner updates and atomic for
workflows that include reads or child insertions.

Concurrent child mutations update the total in the same TP as their inputs. A shared
stored total is intentionally a shared conflict point. Restarted transactions
recompute deltas from current values; failed uniqueness, validation, or later work
rolls back source nodes, totals, and indexes together. Ordinary save retains its
whole-value last-writer-wins semantics. Use atomic read/modify/write for dependent
business calculations; stored sums do not provide optimistic record revisions.

## Compatibility and other writers

Adding, removing, or changing a stored dependency requires a reviewed named Transform;
a default-zero additive backfill cannot initialize existing quantities correctly.
The transform executor materializes and pins target totals before rebuilding indexes.
No installed database is converted just because a new version opens it. Record layout
one, scalar Decimal encoding, and unrelated schema bytes remain unchanged.

Generated M writers reject these hierarchical/derived declarations under
[ADR 0013](../adr/0013-explicit-m-writer-contract.md). Raw SET/KILL bypasses the
invariants and remains unsupported. General Python compute callbacks, owned-level
stored sums, dependency cycles, reference traversal, and automatic object tracking
remain outside this API.
