# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from mccann_hub.yhmf import Database, Field, Model, Path, Reference, Registry, indexes
from mccann_hub.yhmf.errors import QueryError, StorageError
from tests.mutation_models import Lot, Product, Warehouse, warehouse
from tests.test_partial_mutations import ObservedStore


def setup(store, size=3, roots=1):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Warehouse))
    db.insert(Product(id="P", name="Product"))
    for number in range(roots):
        record = warehouse(size)
        record.id = f"W{number}"
        record.bins["A"].lots["0"].product = Reference[Product](key="P")
        db.insert(record)
    observed.operations.clear()
    return db, observed


def query(db):
    return db.query(Warehouse).where(**{"bins.lots.product": Reference[Product](key="P")})


def test_occurrences_read_matching_children_without_materializing_unrelated_lots(store):
    db, observed = setup(store, size=1000)
    hits = query(db).occurrences(limit=25)
    assert len(hits) == 1 and type(hits[0].record) is Lot
    assert hits[0].root_key == "W0" and hits[0].at == ("bins", "A", "lots", "0")
    assert hits[0].field == "bins.lots.product"
    assert hits[0].record.product == Reference[Product](key="P")
    assert not any(b"s:1" in path.subscripts for _, path in observed.operations)
    assert len(observed.operations) < 70


def test_occurrence_cursor_pages_all_hits_without_duplicates_in_physical_order(store):
    db, _ = setup(store, roots=3)
    db.patch(
        Warehouse,
        "W0",
        at=("bins", "A", "lots", "1"),
        changes={"product": Reference[Product](key="P")},
    )
    expected = query(db).occurrences(limit=100)
    result, cursor = [], None
    for _ in range(20):
        page = query(db).occurrences(limit=1, after=cursor)
        if not page:
            break
        result.extend(page)
        cursor = page[-1].cursor
    assert [(hit.root_key, hit.at) for hit in result] == [
        (hit.root_key, hit.at) for hit in expected
    ]
    assert len({hit.cursor for hit in result}) == 4
    assert [hit.root_key for hit in result] == ["W0", "W0", "W1", "W2"]
    with pytest.raises(QueryError, match="belong"):
        db.query(Warehouse).where(**{"bins.lots.product": None}).occurrences(after=cursor)


def test_deleted_cursor_occurrences_can_still_advance(store):
    db, _ = setup(store, roots=2)
    first = query(db).occurrences(limit=1)[0]
    db.delete_child(Warehouse, first.root_key, at=first.at)
    rest = query(db).occurrences(after=first.cursor)
    assert len(rest) == 1 and rest[0].root_key == "W1"


@pytest.mark.parametrize(
    "token", ["not a cursor", "x" * 2049, "e30=", 1], ids=["text", "too-long", "json", "type"]
)
def test_invalid_occurrence_cursors_fail_clearly(store, token):
    db, _ = setup(store)
    with pytest.raises(QueryError):
        query(db).occurrences(after=token)


@pytest.mark.parametrize("operation", ["all", "occurrences"])
@pytest.mark.parametrize("corruption", ["missing_child", "wrong_value", "wrong_digest"])
def test_another_matching_child_cannot_hide_a_corrupt_concrete_occurrence(
    store, operation, corruption
):
    db, _ = setup(store)
    spec = db.registry.spec(Warehouse)
    definition = next(d for d in indexes.definitions(spec) if d.name == "bins.lots.quantity")
    root = indexes.index_root(spec, definition, Decimal(0)).child(b"s:W0")
    if corruption == "wrong_value":
        db.patch(Warehouse, "W0", at=("bins", "A", "lots", "1"), changes={"quantity": Decimal(1)})
        child = "s:1"
    else:
        child = "s:absent"
    location = json.dumps(
        ["bins", "s:A", "lots", child, "quantity"], separators=(",", ":")
    ).encode()
    digest = hashlib.sha256(location).hexdigest().encode()
    if corruption == "wrong_digest":
        digest = b"a" * 64
    store.set(root.child(digest), location)
    selected = db.query(Warehouse).where(**{"bins.lots.quantity": Decimal(0)})
    with pytest.raises(StorageError):
        getattr(selected, operation)(limit=100)


def test_occurrence_projection_requires_an_equality_index(store):
    db, _ = setup(store)
    with pytest.raises(QueryError, match="equality index"):
        db.query(Warehouse).where(id="W0").occurrences()


@pytest.mark.parametrize("root_key,child_key", [("", "雪"), ("0", "01"), ("雪", "🧭")])
def test_occurrence_locations_and_cursors_preserve_unusual_string_keys(store, root_key, child_key):
    db = Database(store, Registry(Product, Warehouse))
    db.insert(Product(id="P", name="Product"))
    root = warehouse(0)
    root.id = root_key
    root.bins["A"].lots[child_key] = Lot(id=child_key, product=Reference[Product](key="P"))
    db.insert(root)
    hit = query(db).occurrences(limit=1)[0]
    assert hit.root_key == root_key and hit.at == ("bins", "A", "lots", child_key)
    assert query(db).occurrences(after=hit.cursor) == []


def test_embedded_and_root_index_owners_keep_their_complete_model_semantics(store):
    class Address(Model):
        city: str = Field(index=True)

    class Root(Warehouse):
        address: Address | None = None

    db = Database(store, Registry(Product, Root))
    db.insert(Root(id="W", address=Address(city="Chicago")))
    hit = db.query(Root).where(**{"address.city": "Chicago"}).occurrences()[0]
    assert type(hit.record) is Address and hit.at == ("address",)
    root_hit = db.query(Root).where(name="Main").occurrences()[0]
    assert type(root_hit.record) is Root and root_hit.at == ()
    assert root_hit.record.address.city == "Chicago"


def test_occurrence_reads_do_not_bypass_validation_of_other_owner_fields(store):
    db, observed = setup(store)
    path = Path(Warehouse.__global__, (b"s:W0", b"bins", b"s:A", b"lots", b"s:0", b"quantity"))
    store.set(path, b"d:-1")
    with pytest.raises(ValidationError):
        query(db).occurrences()
    assert not any(method in {"set", "delete"} for method, _ in observed.operations)


def test_occurrence_reads_check_the_unique_claim(store):
    db, _ = setup(store)
    db.patch(Warehouse, "W0", at=("bins", "A", "lots", "0"), changes={"serial": "claimed"})
    spec = db.registry.spec(Warehouse)
    root = spec.path("W0")
    entry = next(
        e for e in indexes.entries(spec, root, db.get(Warehouse, "W0")) if e.claim_path is not None
    )
    store.delete_tree(entry.claim_path)
    with pytest.raises(StorageError, match="Unique claim"):
        db.query(Warehouse).where(**{"bins.lots.serial": "claimed"}).occurrences()
