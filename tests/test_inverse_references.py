# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json

import pytest

from mccann_hub.yhmf import Database, Path, Registry
from mccann_hub.yhmf.errors import NotFound, QueryError, StorageError
from tests.test_partial_mutations import ObservedStore
from tests.test_references import Details, Line, Order, Product, ref


def setup(store):
    observed = ObservedStore(store)
    db = Database(observed, Registry(Product, Order))
    db.insert(Product(id="P42", name="Product"))
    db.insert(Product(id="P2", name="Other"))
    observed.operations.clear()
    return db, observed


def test_inverse_pages_read_only_matching_owned_sources_without_a_scan(store):
    db, observed = setup(store)
    db.insert(
        Order(
            id=1,
            lines={
                str(i): Line(number=str(i), product=ref("P42" if i == 0 else "P2"))
                for i in range(1000)
            },
        )
    )
    observed.operations.clear()
    hits = db.referrers(ref(), source=Order, limit=25)
    assert len(hits) == 1 and type(hits[0].record) is Line
    assert hits[0].root_key == 1 and hits[0].at == ("lines", "0")
    assert hits[0].field == "lines.product"
    assert not any(b"s:1" in path.subscripts for _, path in observed.operations)
    assert ("get", Path(Product.__global__, (b"s:P42", b"name"))) not in observed.operations
    assert len(observed.operations) < 70


def test_inverse_pages_preserve_root_key_order_and_distinct_field_occurrences(store):
    db, _ = setup(store)
    for key in (10, 2):
        db.insert(
            Order(
                id=key,
                product=ref(),
                details=Details(alternate=ref()),
                lines={"L": Line(number="L", product=ref())},
            )
        )
    expected = db.referrers(ref(), source=Order)
    result, cursor = [], None
    for _ in range(20):
        page = db.referrers(ref(), source=Order, limit=1, after=cursor)
        if not page:
            break
        result.extend(page)
        cursor = page[-1].cursor
    assert [(hit.root_key, hit.at, hit.field) for hit in result] == [
        (hit.root_key, hit.at, hit.field) for hit in expected
    ]
    assert [hit.root_key for hit in result] == [2, 2, 2, 10, 10, 10]
    assert {hit.field for hit in result} == {"product", "details.alternate", "lines.product"}
    with pytest.raises(QueryError, match="belong"):
        db.referrers(ref("P2"), source=Order, after=cursor)


def test_inverse_entries_follow_reference_changes_child_deletion_and_rollback(store):
    db, _ = setup(store)
    db.insert(Order(id=1, lines={"L": Line(number="L", product=ref())}))
    db.patch(Order, 1, at=("lines", "L"), changes={"product": ref("P2")})
    assert db.referrers(ref(), source=Order) == []
    assert len(db.referrers(ref("P2"), source=Order)) == 1

    def abort(tx):
        tx.delete_child(Order, 1, at=("lines", "L"))
        assert tx.referrers(ref("P2"), source=Order) == []
        raise RuntimeError("abort")

    with pytest.raises(RuntimeError):
        db.atomic(abort)
    assert len(db.referrers(ref("P2"), source=Order)) == 1
    db.delete_child(Order, 1, at=("lines", "L"))
    assert db.referrers(ref("P2"), source=Order) == []


@pytest.mark.parametrize("kind", ["missing", "other_target", "null", "digest"])
def test_another_valid_reference_does_not_hide_a_forged_reverse_occurrence(store, kind):
    db, _ = setup(store)
    db.insert(Order(id=1, product=ref(), lines={"L": Line(number="L", product=ref("P2"))}))
    parts = {
        "missing": ["lines", "s:absent", "product"],
        "other_target": ["lines", "s:L", "product"],
        "null": ["details", "alternate"],
        "digest": ["product"],
    }[kind]
    raw = json.dumps(parts, separators=(",", ":")).encode()
    digest = b"a" * 64 if kind == "digest" else hashlib.sha256(raw).hexdigest().encode()
    root = Path("^YHMFRef", (b"YHMFTestProduct", b"s:P42"))
    store.set(root.child(b"YHMFTestOrder", b"1", digest), raw)
    store.set(root, b"2")
    with pytest.raises(StorageError):
        db.referrers(ref(), source=Order)


def test_inverse_query_requires_an_existing_target(store):
    db, _ = setup(store)
    with pytest.raises(NotFound, match="Reference target"):
        db.referrers(ref("absent"), source=Order)
