# SPDX-License-Identifier: GPL-3.0-or-later
"""UTF-8 record bytes and ASCII JSON identities match Python in both M modes."""

import json
import subprocess
from ctypes.util import find_library
from pathlib import Path as FilePath
from typing import Literal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Reference, Registry
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_m_writers import MRecord

m_writer = test_m_writers.m_writer


def test_unicode_finite_domain_exports_without_metadata_conversion(store):
    class Row(Model):
        __global__ = MRecord.__global__
        id: str = Field(primary_key=True)
        value: Literal["caf\u00e9", "\U0001f600"] = Field(unique=True)

    db = Database(store, Registry(Row))
    assert 'cfg("choicecount","value")="2"' in db.m_routine(Row, name="UnicodeAPI")
    assert not db.inspect_schema(Row).installed


@pytest.fixture(params=["M", "UTF-8"])
def unicode_writer(store, m_writer, request):
    _, _, routine, environment = m_writer
    folder = routine.parent / request.param
    folder.mkdir()
    driver = FilePath(__file__).with_name("m") / "YHMFWTEST.m"
    (folder / driver.name).write_bytes(driver.read_bytes())
    env = dict(environment, ydb_chset=request.param)
    env["ydb_routines"] = str(folder) + " " + env["ydb_routines"]
    if request.param == "UTF-8" and "ydb_icu_version" not in env:
        library = find_library("icuio")
        assert library and ".so." in library, (
            "UTF-8 native qualification requires versioned ICU libraries"
        )
        env["ydb_icu_version"] = library.split(".so.", 1)[1]
    root = Path("^YHMFTestStorage", (b"writerInput",))

    def bind(model=MRecord, *targets):
        db = Database(store, Registry(*targets, model))
        replace_source(folder / "YHMFTAPI.m", db.m_routine(model, name="YHMFTAPI"))

        def run(operation, data):
            store.delete_tree(root)
            for field, value in data.items():
                store.set(
                    root.child(field.encode()), value.encode() if isinstance(value, str) else value
                )
            result = subprocess.run(
                [str(FilePath(env["ydb_dist"]) / "yottadb"), "-run", operation + "^YHMFWTEST"],
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
                check=True,
            )
            assert not result.stderr, result.stderr
            return result.stdout.strip()

        return db, run

    return bind


@pytest.mark.native
def test_unicode_keys_values_and_index_claims_match_python(store, unicode_writer):
    db, run = unicode_writer()
    values = {
        "id": "\u5009\U0001f600",
        "sku": "caf\u00e9",
        "name": "e\u0301 \u96ea \U0001f600",
        "quantity": "1",
    }
    assert run("insert", values) == "ok"
    expected = Database(MemoryStore(), Registry(MRecord))
    expected.insert(MRecord.model_validate(values | {"quantity": 1}))
    roots = (MRecord.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected.store, roots)
    assert db.query(MRecord).where(sku="caf\u00e9").all()[0].id == values["id"]
    assert run("replace", values | {"sku": "\U0001f9ea"}) == "ok"
    expected.save(MRecord.model_validate(values | {"sku": "\U0001f9ea", "quantity": 1}))
    assert dump(store, roots) == dump(expected.store, roots)
    assert db.check_indexes(MRecord).issues == ()
    assert run("delete", {"id": values["id"]}) == "ok"
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("text", ["\U0001f600\u00e9", "e\u0301", "\u96ea\u5009"])
def test_string_constraints_count_codepoints_in_both_modes(unicode_writer, text):
    class Pair(MRecord):
        name: str = Field(min_length=2, max_length=2)

    db, run = unicode_writer(Pair)
    values = {"id": "A", "sku": "S", "name": text, "quantity": "1"}
    assert run("insert", values) == "ok"
    assert db.get(Pair, "A").name == text
    assert run("replace", values | {"name": text[0]}) == "invalid"
    assert run("replace", values | {"name": text + "x"}) == "invalid"


@pytest.mark.native
@pytest.mark.parametrize(
    "raw", [b"\x80", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82"]
)
def test_invalid_utf8_input_and_stored_strings_are_rejected_without_writes(
    store, unicode_writer, raw
):
    db, run = unicode_writer()
    values = {"id": "A", "sku": "S", "name": "Stock", "quantity": "1"}
    assert run("insert", values | {"name": raw}) == "invalid"
    assert not db.inspect_schema(MRecord).installed
    assert run("insert", values) == "ok"
    store.set(db.registry.spec(MRecord).path("A").child(b"name"), b"s:" + raw)
    roots = (MRecord.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    before = dump(store, roots)
    assert run("replace", values) == "corrupt"
    assert dump(store, roots) == before


@pytest.mark.native
@settings(
    max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(text=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=100))
def test_generated_m_json_quoting_matches_python_ascii_escaping(unicode_writer, text):
    _, run = unicode_writer()
    assert run("jsonquote", {"value": text}) == json.dumps(
        text, ensure_ascii=True, separators=(",", ":")
    )


@pytest.mark.native
def test_unicode_choice_members_and_unique_bytes_match_python(store, unicode_writer):
    class Row(Model):
        __global__ = MRecord.__global__
        id: str = Field(primary_key=True)
        value: Literal["caf\u00e9", "\U0001f600"] = Field(unique=True)

    db, run = unicode_writer(Row)
    assert run("insert", {"id": "倉", "value": "😀"}) == "ok"
    expected = Database(MemoryStore(), Registry(Row))
    expected.insert(Row(id="倉", value="😀"))
    roots = (Row.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected.store, roots)
    before = dump(store, roots)
    assert run("replace", {"id": "倉", "value": "雪"}) == "invalid"
    assert dump(store, roots) == before
    assert db.check_indexes(Row).issues == ()


@pytest.mark.native
def test_unicode_reference_keys_and_reverse_locations_match_python(store, unicode_writer):
    class Product(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)

    class Link(Model):
        __global__ = MRecord.__global__
        id: str = Field(primary_key=True)
        product: Reference[Product] = Field(index=True)

    db, run = unicode_writer(Link, Product)
    db.insert(Product(id="倉😀"))
    assert run("insert", {"id": "L雪", "product": "倉😀"}) == "ok"
    expected = Database(MemoryStore(), Registry(Product, Link))
    expected.insert(Product(id="倉😀"))
    expected.insert(Link(id="L雪", product=Reference[Product](key="倉😀")))
    roots = (
        Product.__global__,
        Link.__global__,
        "^YHMFSchema",
        "^YHMFIndex",
        "^YHMFUnique",
        "^YHMFRef",
    )
    assert dump(store, roots) == dump(expected.store, roots)
    assert db.referrers(Reference[Product](key="倉😀"), source=Link)[0].root_key == "L雪"
    assert db.check_indexes(Link).issues == ()


@pytest.mark.native
def test_unicode_owned_keys_produce_python_location_digests(store, unicode_writer):
    from tests.test_m_choice_writers import (
        ChoiceBin,
        ChoiceChild,
        ChoiceLocation,
        ChoiceRoot,
        Rank,
        State,
    )

    db, run = unicode_writer(ChoiceRoot)
    root = ChoiceRoot(
        id="MAIN", locations={"倉": ChoiceLocation(id="倉", bins={"B😀": ChoiceBin(id="B😀")})}
    )
    db.insert(root)
    expected = Database(MemoryStore(), Registry(ChoiceRoot))
    expected.insert(root)
    assert (
        run(
            "ownedinsert",
            {
                "_location": "倉",
                "_bin": "B😀",
                "id": "LOT-001",
                "kind": "receive",
                "state": "open",
                "rank": "-1",
            },
        )
        == "ok"
    )
    expected.insert_child(
        ChoiceRoot,
        "MAIN",
        collection=("locations", "倉", "bins", "B😀", "balances"),
        record=ChoiceChild(id="LOT-001", kind="receive", state=State.OPEN, rank=Rank.LOW),
    )
    roots = (ChoiceRoot.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected.store, roots)
    assert db.check_indexes(ChoiceRoot).issues == ()
