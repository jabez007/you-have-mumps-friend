# SPDX-License-Identifier: GPL-3.0-or-later
"""Null object presence differs from an object whose scalar fields are null."""

import pytest

from mccann_hub.yhmf import ChildField, Children, Database, MemoryStore, Reference, Registry
from tests import test_m_embedded_writers
from tests.adversarial_support import dump
from tests.test_m_embedded_writers import Envelope, Position, Product, roots
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_embedded_writers.m_writer
embedded_writer = test_m_embedded_writers.embedded_writer


class NullableEnvelope(Envelope):
    position: Position | None = None


def test_nullable_embedded_export_preserves_existing_python_contract(store):
    db = Database(store, Registry(Product, NullableEnvelope))
    source = db.m_routine(NullableEnvelope, name="NullableAPI")
    assert 'cfg("nodes","","nullobject","position")="1"' in source
    assert 'cfg("nodes","","variable")="1"' in source
    assert not db.inspect_schema(NullableEnvelope).installed


@pytest.mark.native
def test_nullable_object_transitions_and_absent_claims_match_python(store, embedded_writer):
    db, run, _ = embedded_writer(NullableEnvelope)
    expected = Database(MemoryStore(), Registry(Product, NullableEnvelope))
    for target in (db, expected):
        target.insert(Product(id="P"))
    for key in ("A", "B"):
        assert run("insert", {"id": key, "name": key, "position": None}) == "ok"
        expected.insert(NullableEnvelope(id=key, name=key))
    assert dump(store, roots()) == dump(expected.store, roots())
    value = {"id": "A", "name": "Changed", "position": {"product": "P", "label": "L"}}
    assert run("replace", value) == "ok"
    expected.save(
        NullableEnvelope(
            id="A",
            name="Changed",
            position=Position(product=Reference[Product](key="P"), label="L"),
        )
    )
    assert dump(store, roots()) == dump(expected.store, roots())
    before = dump(store, roots())
    assert run("replace", value | {"id": "B"}) == "unique"
    assert dump(store, roots()) == before
    assert run("replace", {"id": "A", "name": "A", "position": None}) == "ok"
    expected.save(NullableEnvelope(id="A", name="A"))
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.referrers(Reference[Product](key="P"), source=NullableEnvelope) == []
    assert db.check_indexes(NullableEnvelope).issues == ()
    for key in ("A", "B"):
        assert run("delete", {"id": key}) == "ok"
        expected.delete(NullableEnvelope, key)
    assert dump(store, roots()) == dump(expected.store, roots())


@pytest.mark.native
@pytest.mark.parametrize(
    "value", ["n:", {"%null": "0"}, {"%null": "01"}, {"%null": "1", "extra": "x"}]
)
def test_malformed_null_inputs_and_missing_objects_leave_no_state(store, embedded_writer, value):
    db, run, _ = embedded_writer(NullableEnvelope)
    before = dump(store, roots())
    assert run("insert", {"id": "A", "name": "A", "position": value}) == "invalid"
    assert run("insert", {"id": "A", "name": "A"}) == "invalid"
    assert dump(store, roots()) == before
    assert not db.inspect_schema(NullableEnvelope).installed


@pytest.mark.native
def test_null_marker_with_descendants_is_corrupt_and_cannot_be_erased(store, embedded_writer):
    db, run, _ = embedded_writer(NullableEnvelope)
    db.insert(NullableEnvelope(id="A", name="A"))
    store.set(db.registry.spec(NullableEnvelope).path("A").child(b"position", b"label"), b"s:extra")
    before = dump(store, roots())
    assert run("delete", {"id": "A"}) == "corrupt"
    assert dump(store, roots()) == before


@pytest.mark.native
def test_object_presence_changes_survive_restart_after_nested_writes(store, embedded_writer):
    db, run, routine = embedded_writer(NullableEnvelope)
    db.insert(Product(id="P"))
    db.insert(NullableEnvelope(id="A", name="A"))
    source = routine.read_text()
    marker = " ; YHMF embedded changes complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    assert (
        run("replace", {"id": "A", "name": "A", "position": {"product": "P", "label": "L"}}) == "ok"
    )
    assert db.get(NullableEnvelope, "A").position.label == "L"
    assert run("replace", {"id": "A", "name": "A", "position": None}) == "ok"
    assert db.get(NullableEnvelope, "A").position is None
    assert db.check_indexes(NullableEnvelope).issues == ()


@pytest.mark.native
def test_variable_embedded_capacity_counts_existing_and_incoming_presence_exactly(
    store, embedded_writer
):
    from pydantic import create_model

    from tests.test_m_embedded_writers import OwnedRoot, PositionedChild

    wide = create_model(
        "WidePosition", __base__=Position, **{f"extra{i}": (str, "x") for i in range(9)}
    )
    child = create_model("OptionalChild", __base__=PositionedChild, position=(wide | None, None))
    root = create_model(
        "VariableRoot", __base__=OwnedRoot, items=(Children[child], ChildField(key="id"))
    )
    db, run, _ = embedded_writer(root)
    db.insert(Product(id="P"))
    children = {
        str(i): child(id=str(i), position=wide(product=Reference[Product](key="P"), label=str(i)))
        for i in range(712)
    }
    children.update({f"null{i}": child(id=f"null{i}") for i in range(5)})
    db.insert(root(id="MAIN", items=children))
    # 6 metadata/root nodes + 712 * 14 populated child nodes + 5 * 3 null child nodes.
    assert run("embeddedchild", {"id": "NULL", "position": None}) == "ok"
    assert (
        run(
            "embeddedchild",
            {
                "id": "OVER",
                "position": {
                    "product": "P",
                    "label": "OVER",
                    **{f"extra{i}": "x" for i in range(9)},
                },
            },
        )
        == "node_budget"
    )
    assert db.count(root, "MAIN", collection=("items",)) == 718
    assert db.get(root, "MAIN", at=("items", "NULL")).position is None
    assert len(db.referrers(Reference[Product](key="P"), source=root, limit=1000)) == 712
    assert db.check_indexes(root).issues == ()
