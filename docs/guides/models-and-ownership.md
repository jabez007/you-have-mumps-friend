# Define models and owned data

YHMF has three ways to relate data, and each one means something different in storage.

| Relationship | Declare with | Stored | Identity |
| --- | --- | --- | --- |
| Embedded object | A field whose type is another `Model` | Under the parent | None of its own |
| Owned children | `Children[Child]` and `ChildField(key=...)` | Under the parent, by key | Parent path plus child key |
| Reference | `Reference[Target]` | As a key, with the target elsewhere | The target's own key |

Choose by ownership and lifecycle, not by reflex. Moving an owned child to another
parent changes its identity. If a record keeps its identity across moves, make it a
top-level model and use a [reference](references.md).

## Declare a top-level model

A top-level model has a `__global__` and exactly one primary key:

```python
from decimal import Decimal

from mccann_hub.yhmf import ChildField, Children, Database, Field, MemoryStore, Model, Registry


class Product(Model):
    __global__ = "^Product"
    id: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)
```

Field types include `str`, `int`, `bool`, `float`, `Decimal`, `bytes`, date, aware
datetime, UUID, Enum/Literal choices, nullable variants, embedded models, `Children`,
scalar lists, and `Reference`. Keys must match their declared str/int type.

The registry rejects overlapping globals, aliases, unsupported field types, and missing
or ambiguous primary keys.

Models default to `extra="forbid"`.

The registry rejects `extra="allow"`; only declared fields have storage locations.

## Choose the right write

| Operation | Use it to |
| --- | --- |
| `db.insert(record)` | Create a root and reject an existing key. |
| `db.save(record)` | Replace a whole aggregate, or insert it if absent. |
| `db.patch(...)` | [Change scalar or reference fields](partial-updates.md). |
| `db.insert_child(...)`, `db.delete_child(...)` | [Change keyed ownership](partial-updates.md#insert-and-delete-keyed-children). |
| `db.delete(model, key)` | Delete a root. Returns whether a record existed. |

`db.get` raises `NotFound` when the record is absent.

## Declare owned children

Owned models have no `__global__`. The parent controls their persistence and deletion.
`Children` is a dictionary, and each key must match the child's primary key:

```python
class Lot(Model):
    number: str = Field(primary_key=True)
    quantity: Decimal = Field(default=Decimal("0"), ge=0)


class Bin(Model):
    code: str = Field(primary_key=True)
    lots: Children[Lot] = ChildField(key="number")


class Warehouse(Model):
    __global__ = "^Warehouse"
    id: int = Field(primary_key=True)
    name: str = "Main"
    bins: Children[Bin] = ChildField(key="code")


db = Database(MemoryStore(), Registry(Warehouse))
db.insert(
    Warehouse(
        id=7,
        bins={"A01": Bin(code="A01", lots={"L-001": Lot(number="L-001")})},
    )
)
assert db.get(Warehouse, 7).bins["A01"].lots["L-001"].quantity == Decimal("0")
```

Read one collection without loading the warehouse:

```python
page = db.children(Warehouse, 7, collection=("bins", "A01", "lots"), limit=25)
assert [lot.number for lot in page] == ["L-001"]
assert db.count(Warehouse, 7, collection=("bins", "A01", "lots")) == 1
assert db.children(
    Warehouse, 7, collection=("bins", "A01", "lots"), after="L-001", limit=25
) == []
```

`children` accepts limits from 1 through 1000 and returns complete child models in native
key order. Pass the last returned child's key as `after`. Cursor types must match the
child declaration; numeric-looking string keys remain strings. The cursor need not still
exist. Pages run in separate transactions unless called inside the same `atomic` callback.
Concurrent inserts before the cursor can be omitted, and inserts after it can appear;
pages do not share a snapshot. Null collections and absent owners raise `NotFound`;
empty collections return an empty page and count zero.

`count` reads the transactional c:N marker, without enumerating keys or loading children.
It is not an audit of raw writes. A page validates the selected children's presence,
stored values, and identities. It does not run ancestor validators over unselected
descendants. Use a complete root read when those application checks are required.

To coordinate updates across an aggregate, edit the loaded aggregate and save its root
inside [`atomic`](transactions.md). For direct changes to existing owned paths, use the
[partial updates](partial-updates.md) instead.

Ownership declarations cannot be recursive. Reads and writes also enforce
[size limits](../reference/limits.md) on collections and records.

## Keep persistent fields explicit

The registry rejects `extra="allow"` on a root or any owned model. Unknown values
accepted by that policy have no declared storage field. Declare them as model
fields instead. `extra="ignore"` remains available when input data intentionally
contains values that the application discards.

Disabling instance, default, or assignment validation does not bypass write-time
validation. The mapper rebuilds plain owned input and validates it before writing.
Setting `allow_inf_nan=True` does not permit nonfinite stored scalars. See the
[configuration compatibility policy](../schema-compatibility.md#model-configuration-and-declared-data).

## Keep registered declarations fixed

A registry compiles field adapters and the partial-validation policy for each declared
model. Reuse that registry during the application's lifetime. Do not modify annotations,
configuration, validators, or field metadata after registration. A rebuilt Pydantic
schema, changed model configuration, global name, or root/owned application revision
raises `SchemaError` on access. Other in-place
Python mutations are unsupported; they are not a schema-migration mechanism.

For an application schema change, define a new model class and registry, then inspect
and migrate the installed schema. Equal persistence fingerprints do not make Python
validation behavior interchangeable: compiled rules belong to that registry's exact
declarations. This compilation does not cache database state. Every transaction still
checks the live installed schema snapshot and fingerprint.

## Reuse field declarations

Ordinary Python inheritance reuses fields before the registry compiles a model:

```python
class Identified(Model):
    id: str = Field(primary_key=True, min_length=1)


class Catalog(Identified):
    __global__ = "^ReusableCatalog"
    sku: str = Field(unique=True)


catalog_db = Database(MemoryStore(), Registry(Catalog))
catalog_db.insert(Catalog(id="C", sku="SKU"))
assert catalog_db.get(Catalog, "C").sku == "SKU"
```

Identified is a declaration base, not a registered root. Each independent model declares
its own global. Owned models can inherit the same key field without declaring a global.
The resulting fields have ordinary names and index paths; the Python base introduces no
stored relationship. Use an embedded object or Reference when the shared data has an
ownership or lifecycle relationship instead.

A subclass extending a stored root inherits that global unless it explicitly declares
another one. Review its schema difference and migrate before use. A registry accepts one
model for each global, so registering both old and replacement declarations is rejected.
Shared-global variants and runtime class mutation are not implied by field inheritance.
[ADR 0007](../adr/0007-compile-time-model-reuse.md) records the choice and alternatives.

## Compute a virtual value

Use a Python property for a calculation on a loaded object. Pydantic computed_field
also includes it in the object's normal model_dump output:

```python
from pydantic import computed_field


class VirtualCatalog(Catalog):
    @computed_field
    @property
    def display_name(self) -> str:
        return f"{self.id}: {self.sku}"


virtual_db = Database(MemoryStore(), Registry(VirtualCatalog))
item = VirtualCatalog(id="V", sku="ITEM")
virtual_db.insert(item)
assert virtual_db.get(VirtualCatalog, "V").display_name == "V: ITEM"
```

The property reads this Python value object, not live database state. Persistence
does not evaluate it or store its result. Adding it preserves the schema contract,
including supported legacy snapshots. It cannot be patched, indexed, used as a
stored key, or passed to a query predicate. Use the original declared fields for
writes; model_dump with computed output is a presentation mapping, not persistence
input. Save the model itself instead.

Prefer an ordinary uncached property when fields may change. Python cached_property
does not invalidate automatically when you patch the database or modify an object.
No dependency graph or stored rollup maintenance is implied. The inventory
Warehouse.total_on_hand sums a loaded warehouse using a local Decimal context;
loading that aggregate costs work proportional to its data. For targeted inventory
queries, use indexed balance occurrences instead.

## Calendar dates and UTC instants

Use standard date and timezone-aware datetime values:

```python
from datetime import UTC, date, datetime
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Registry


class CalendarEntry(Model):
    __global__ = "^CalendarEntry"
    id: str = Field(primary_key=True)
    day: date = Field(ge=date(2000, 1, 1), index=True)
    instant: datetime | None = Field(default=None, unique=True)


calendar = Database(MemoryStore(), Registry(CalendarEntry))
calendar.insert(CalendarEntry(id="E", day=date(2026, 11, 6), instant=datetime(2026, 11, 6, 12, tzinfo=UTC)))
assert calendar.query(CalendarEntry).where(day=date(2026, 11, 6)).all()[0].id == "E"
assert calendar.get(CalendarEntry, "E").instant.tzinfo is UTC
```

Datetimes store UTC instants with microsecond precision. Naive values and instants
outside years 0001–9999 after UTC conversion raise StorageError. Equivalent offsets
share equality and uniqueness values. Reads return UTC, without the original zone
or fold presentation. Use pure validators that accept this persisted representation.
Date/datetime defaults and bounds must be representable; datetime bounds need a timezone.
The new codecs do not change existing schema identities. Converting a stored string
field requires an explicit named record transform.

## UUID values

Use uuid.UUID for identifier-valued fields. Equality and unique indexes compare the
complete 128-bit identifier. Reads return UUID objects, and defaults can be UUID
objects reconstructed by the existing literal-default migration path. Stored UUIDs
use lowercase hyphenated text with a dedicated type tag; alternative raw encodings
fail safely. Primary keys remain str/int, so use a unique UUID scalar when the model
also needs the existing native key contract. Version-specific Pydantic UUID aliases
are outside the supported metadata set for now.

## Finite choices

Use ordinary Enum, StrEnum, IntEnum, or Literal with homogeneous str/int/bool values:

```python
from enum import StrEnum
from typing import Literal


class Status(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class WorkItem(Model):
    __global__ = "^WorkItem"
    id: str = Field(primary_key=True)
    status: Status = Field(default=Status.OPEN, index=True)
    kind: Literal["receipt", "issue"] = "receipt"


work = Database(MemoryStore(), Registry(WorkItem))
work.insert(WorkItem(id="A"))
work.patch(WorkItem, "A", changes={"status": Status.CLOSED})
assert work.query(WorkItem).where(status=Status.CLOSED).all()[0].status is Status.CLOSED
```

Strict Enum fields take members in Python input and queries. Reads restore members
from the stored primitive value. Unknown raw values fail even if the enum implements
_missing_. Literal fields remain primitive values. Use a nullable union for nulls.
Flags, empty/mixed/nonprimitive domains, and Enum use_enum_values=True are unsupported.
Primary keys remain plain str/int. Names, aliases, and ordering do not change schema
identity; changing an Enum's stored domain needs an explicit named transform. Literal
constraint changes use the existing validation-only migration. Defaults and indexes
use the same transaction guarantees as other scalars.

## Scalar lists

Lists of supported scalar or finite-choice values have native numbered item nodes:

```python
class TaggedItem(Model):
    __global__ = "^TaggedItem"
    id: str = Field(primary_key=True)
    tags: list[str] = Field(default_factory=list, index=True, max_length=8)
    samples: list[int | None] | None = None


tagged = Database(MemoryStore(), Registry(TaggedItem))
tagged.insert(TaggedItem(id="A", tags=["cold", "fragile"]))
tagged.patch(TaggedItem, "A", changes={"samples": [1, None, 2]})
assert tagged.query(TaggedItem).where(tags=["cold", "fragile"]).all()[0].id == "A"
assert tagged.query(TaggedItem).where(tags=["fragile", "cold"]).all() == []
```

List equality includes order and duplicates. It is not a membership query. Patches
replace the logical list and write only changed ordinals; use patch_many to group
changes. Empty, null, and missing remain distinct. Lists have at most 1000 items and
share the root's 10,000-node budget with every owned record. Growth checks may read
other variable-width branch markers, without loading their item values. Long indexed
lists can exceed native key limits before reaching the item limit.

Nested/model/reference lists and Annotated item declarations are rejected. Use
whole-field constraints or validators for item policies; those hooks follow existing
partial-validation restrictions on owners with descendants. New list fields and
item type changes require an explicit named record transform initially. Validation
or index changes on an existing list use the normal migration executor.
