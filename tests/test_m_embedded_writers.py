# SPDX-License-Identifier: GPL-3.0-or-later
"""Embedded M writes reuse the ordinary owner codec and transactional transitions."""

import subprocess
from pathlib import Path as FilePath

import pytest
from pydantic import model_validator

from mccann_hub.yhmf import (
    ChildField,
    Children,
    Database,
    Field,
    MemoryStore,
    Model,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.errors import SchemaError
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source

m_writer = test_m_writers.m_writer


class Product(Model):
    __global__ = "^YHMFTestOrder"
    id: str = Field(primary_key=True)


class Position(Model):
    product: Reference[Product] = Field(index=True)
    label: str = Field(min_length=1, unique=True)


class Envelope(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    name: str = Field(min_length=1)
    position: Position


def test_nonnullable_embedded_export_keeps_node_addressing_and_bindings(store):
    db = Database(store, Registry(Product, Envelope))
    source = db.m_routine(Envelope, name="EmbeddedAPI")
    assert 'cfg("nodes","","embedded","position")="position"' in source
    assert not db.inspect_schema(Envelope).installed


@pytest.fixture
def embedded_writer(store, m_writer):
    _, _, routine, environment = m_writer
    root = Path("^YHMFTestStorage", (b"writerInput",))

    def bind(model=Envelope):
        db = Database(store, Registry(Product, model))
        replace_source(routine, db.m_routine(model, name="YHMFTAPI"))

        def run(operation, data):
            store.delete_tree(root)

            def stage(at, value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        stage(at.child(key.encode()), item)
                elif value is None:
                    store.set(at.child(b"%null"), b"1")
                else:
                    store.set(at, value.encode())

            for field, value in data.items():
                stage(root.child(field.encode()), value)
            result = subprocess.run(
                [
                    str(FilePath(environment["ydb_dist"]) / "yottadb"),
                    "-run",
                    operation + "^YHMFWTEST",
                ],
                env=environment,
                text=True,
                capture_output=True,
                timeout=20,
                check=True,
            )
            assert all("%YDB-I-DBFILEXT" in line for line in result.stderr.splitlines()), (
                result.stderr
            )
            return result.stdout.strip()

        return db, run, routine

    return bind


def roots():
    return (
        Product.__global__,
        Envelope.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    )


@pytest.mark.native
def test_embedded_insert_replace_patch_delete_match_python_exactly(store, embedded_writer):
    db, run, _ = embedded_writer()
    expected = Database(MemoryStore(), Registry(Product, Envelope))
    for target in (db, expected):
        target.insert(Product(id="P"))
        target.insert(Product(id="Q"))
    values = {"id": "A", "name": "A", "position": {"product": "P", "label": "first"}}
    assert run("insert", values) == "ok"
    expected.insert(
        Envelope(
            id="A", name="A", position=Position(product=Reference[Product](key="P"), label="first")
        )
    )
    assert dump(store, roots()) == dump(expected.store, roots())
    assert run("rootpatch", {"name": "Patched"}) == "ok"
    expected.patch(Envelope, "A", changes={"name": "Patched"})
    assert dump(store, roots()) == dump(expected.store, roots())
    assert run("replace", values | {"position": {"product": "Q", "label": "next"}}) == "ok"
    expected.save(
        Envelope(
            id="A", name="A", position=Position(product=Reference[Product](key="Q"), label="next")
        )
    )
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.query(Envelope).where(**{"position.label": "next"}).all()[0].id == "A"
    assert db.referrers(Reference[Product](key="Q"), source=Envelope)[0].at == ("position",)
    assert db.check_indexes(Envelope).issues == ()
    assert run("delete", {"id": "A"}) == "ok"
    expected.delete(Envelope, "A")
    assert dump(store, roots()) == dump(expected.store, roots())


@pytest.mark.native
@pytest.mark.parametrize(
    "position",
    [
        None,
        "o:1",
        {"label": "x"},
        {"product": "P", "label": ""},
        {"product": "P", "label": "x", "extra": "x"},
    ],
)
def test_invalid_embedded_inputs_leave_source_metadata_and_targets_unchanged(
    store, embedded_writer, position
):
    db, run, _ = embedded_writer()
    db.insert(Product(id="P"))
    before = dump(store, roots())
    assert run("insert", {"id": "A", "name": "A", "position": position}) == "invalid"
    assert dump(store, roots()) == before


@pytest.mark.native
def test_embedded_unique_failure_rolls_back_root_and_reference_changes(store, embedded_writer):
    db, run, _ = embedded_writer()
    db.insert(Product(id="P"))
    db.insert(Product(id="Q"))
    for key in ("A", "B"):
        db.insert(
            Envelope(
                id=key, name=key, position=Position(product=Reference[Product](key="P"), label=key)
            )
        )
    before = dump(store, roots())
    assert (
        run("replace", {"id": "A", "name": "Changed", "position": {"product": "Q", "label": "B"}})
        == "unique"
    )
    assert dump(store, roots()) == before
    assert db.check_indexes(Envelope).issues == ()


def test_embedded_custom_validation_remains_rejected(store):
    class Custom(Position):
        @model_validator(mode="after")
        def validate_position(self):
            return self

    class Customized(Envelope):
        position: Custom

    for model in (Customized,):
        with pytest.raises(SchemaError, match="M writer|M owned|M embedded"):
            Database(store, Registry(Product, model)).m_routine(model, name="EmbeddedAPI")


class NestedPosition(Model):
    address: Position
    code: str = Field(index=True)


class NestedEnvelope(Envelope):
    position: NestedPosition


@pytest.mark.native
def test_nested_embedded_writes_and_native_restarts_preserve_python_layout(store, embedded_writer):
    db, run, routine = embedded_writer(NestedEnvelope)
    db.insert(Product(id="P"))
    expected = Database(MemoryStore(), Registry(Product, NestedEnvelope))
    expected.insert(Product(id="P"))
    source = routine.read_text()
    marker = " ; YHMF embedded changes complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    data = {
        "id": "A",
        "name": "A",
        "position": {"address": {"product": "P", "label": "nested"}, "code": "CODE"},
    }
    assert run("insert", data) == "ok"
    expected.insert(
        NestedEnvelope(
            id="A",
            name="A",
            position=NestedPosition(
                address=Position(product=Reference[Product](key="P"), label="nested"), code="CODE"
            ),
        )
    )
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.referrers(Reference[Product](key="P"), source=NestedEnvelope)[0].at == (
        "position",
        "address",
    )
    assert db.check_indexes(NestedEnvelope).issues == ()
    assert run("delete", {"id": "A"}) == "ok"
    expected.delete(NestedEnvelope, "A")
    assert dump(store, roots()) == dump(expected.store, roots())


@pytest.mark.native
@pytest.mark.parametrize("damage", ["marker", "missing", "extra", "tag"])
def test_corrupt_embedded_data_rolls_back_earlier_root_changes(store, embedded_writer, damage):
    db, run, _ = embedded_writer()
    db.insert(Product(id="P"))
    db.insert(
        Envelope(
            id="A", name="A", position=Position(product=Reference[Product](key="P"), label="first")
        )
    )
    at = db.registry.spec(Envelope).path("A").child(b"position")
    if damage == "marker":
        store.set(at, b"o:0")
    elif damage == "missing":
        store.delete_tree(at.child(b"label"))
    elif damage == "extra":
        store.set(at.child(b"unknown"), b"s:x")
    else:
        store.set(at.child(b"label"), b"i:1")
    before = dump(store, roots())
    assert (
        run(
            "replace", {"id": "A", "name": "Changed", "position": {"product": "P", "label": "next"}}
        )
        == "corrupt"
    )
    assert dump(store, roots()) == before


def test_unrepresentable_empty_objects_and_collections_inside_embedded_objects_fail_export(store):
    class Empty(Model):
        pass

    class Child(Model):
        id: str = Field(primary_key=True)

    class Collection(Model):
        children: Children[Child] = ChildField(key="id")

    class EmptyHost(Envelope):
        position: Empty

    class CollectionHost(Envelope):
        position: Collection

    for model in (EmptyHost, CollectionHost):
        with pytest.raises(SchemaError, match="M embedded|M writers"):
            Database(store, Registry(Product, model)).m_routine(model, name="EmbeddedAPI")


class PositionedChild(Model):
    id: str = Field(primary_key=True)
    position: Position


class OwnedRoot(Model):
    __global__ = Envelope.__global__
    id: str = Field(primary_key=True)
    items: Children[PositionedChild] = ChildField(key="id")


def test_embedded_fields_are_counted_in_fixed_owner_width(store):
    source = Database(store, Registry(Product, OwnedRoot)).m_routine(OwnedRoot, name="EmbeddedAPI")
    assert 'cfg("nodes","items","width")="4"' in source
    assert 'cfg("nodes","items","leaf")="1"' in source


@pytest.mark.native
def test_owned_insert_with_embedded_fields_matches_python_and_preserves_counts(
    store, embedded_writer
):
    db, run, _ = embedded_writer(OwnedRoot)
    expected = Database(MemoryStore(), Registry(Product, OwnedRoot))
    for target in (db, expected):
        target.insert(Product(id="P"))
        target.insert(OwnedRoot(id="MAIN"))
    assert run("embeddedchild", {"id": "A", "position": {"product": "P", "label": "child"}}) == "ok"
    expected.insert_child(
        OwnedRoot,
        "MAIN",
        collection=("items",),
        record=PositionedChild(
            id="A", position=Position(product=Reference[Product](key="P"), label="child")
        ),
    )
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.count(OwnedRoot, "MAIN", collection=("items",)) == 1
    assert db.check_indexes(OwnedRoot).issues == ()


@pytest.mark.native
def test_missing_embedded_reference_rolls_back_initial_metadata_and_indexes(store, embedded_writer):
    db, run, _ = embedded_writer()
    db.insert(Product(id="P"))
    before = dump(store, roots())
    assert (
        run(
            "insert", {"id": "A", "name": "A", "position": {"product": "MISSING", "label": "first"}}
        )
        == "references"
    )
    assert dump(store, roots()) == before
    assert not db.inspect_schema(Envelope).installed


def test_embedded_rollup_sources_are_rejected_until_delta_composition_is_qualified(store):
    from decimal import Decimal

    class Amount(Model):
        quantity: Decimal

    class Child(Model):
        id: str = Field(primary_key=True)
        value: Amount

    class Total(Model):
        __global__ = Envelope.__global__
        id: str = Field(primary_key=True)
        items: Children[Child] = ChildField(key="id")
        total: Decimal = Field(sum_of="items.value.quantity")

    with pytest.raises(SchemaError, match="embedded rollup sources"):
        Database(store, Registry(Total)).m_routine(Total, name="EmbeddedAPI")


@pytest.mark.native
def test_embedded_width_preserves_the_native_record_node_budget(store, embedded_writer):
    from pydantic import create_model

    wide = create_model(
        "WidePosition", __base__=Position, **{f"extra{i}": (str, "x") for i in range(9)}
    )
    child = create_model("WideChild", __base__=PositionedChild, position=(wide, Field()))
    root = create_model(
        "WideRoot", __base__=OwnedRoot, items=(Children[child], ChildField(key="id"))
    )
    db, run, _ = embedded_writer(root)
    db.insert(Product(id="P"))

    def position(label):
        return wide(product=Reference[Product](key="P"), label=label)

    db.insert(
        root(
            id="MAIN",
            items={str(i): child(id=str(i), position=position(str(i))) for i in range(700)},
        )
    )
    for index in range(13):
        key = f"extra{index}"
        assert (
            run(
                "embeddedchild",
                {
                    "id": key,
                    "position": {
                        "product": "P",
                        "label": key,
                        **{f"extra{i}": "x" for i in range(9)},
                    },
                },
            )
            == "ok"
        )
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
    assert db.count(root, "MAIN", collection=("items",)) == 713
    assert db.check_indexes(root).issues == ()
