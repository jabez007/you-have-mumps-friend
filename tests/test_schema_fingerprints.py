# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
from decimal import Decimal
from pathlib import Path as FilePath

import pydantic
import pytest

from mccann_hub.yhmf import Database, Registry, SchemaSnapshot, compare_schemas
from mccann_hub.yhmf.registry import _make_spec
from tests.fingerprint_models import Stock

FIXTURES = FilePath(__file__).with_name("fixtures")


def snapshot(version):
    return SchemaSnapshot((FIXTURES / f"schema-pydantic-{version}.json").read_bytes())


def test_identical_models_have_different_legacy_fingerprints_across_pydantic_versions():
    old = snapshot("2.12.0")
    new = snapshot("2.14.3")
    assert old.fingerprint != new.fingerprint
    assert old.global_name == new.global_name
    assert old.layout_version == new.layout_version == 1
    assert old.schema_version == new.schema_version == 1
    assert {c.kind for c in compare_schemas(old, new)} == {"validation_changed"}
    assert old.to_dict()["declaration"]["properties"]["price"]["anyOf"][1]["pattern"]
    assert "pattern" not in new.to_dict()["declaration"]["properties"]["price"]["anyOf"][1]


def test_current_generator_keeps_qualified_legacy_bytes():
    if pydantic.__version__ not in {"2.12.0", "2.14.3"}:
        pytest.skip("Fingerprint bytes are qualified for 2.12.0 and 2.14.3")
    spec = _make_spec(Stock, legacy=True)
    assert spec.snapshot == snapshot(pydantic.__version__).canonical
    assert spec.fingerprint == hashlib.sha256(spec.snapshot).hexdigest().encode("ascii")


def test_foreign_runtime_metadata_is_compatible_without_any_rewriting(store):
    from mccann_hub.yhmf.schemas import schema_path

    version = "2.12.0" if pydantic.__version__ == "2.14.3" else "2.14.3"
    foreign = snapshot(version)
    root = schema_path(Stock.__global__)
    store.set(root, foreign.fingerprint.encode("ascii"))
    store.set(root.child(b"snapshot"), foreign.canonical)
    db = Database(store, Registry(Stock))
    assert db.inspect_schema(Stock).status == "matching"
    assert db.plan_migration(Stock).is_noop
    db.insert(Stock(id="W", price=Decimal("1")))
    assert db.get(Stock, "W").price == Decimal("1")
    assert store.get(
        Registry(Stock).spec(Stock).path("W").child(b"%m", b"fingerprint")
    ) == foreign.fingerprint.encode("ascii")
    assert store.get(root) == foreign.fingerprint.encode("ascii")
    assert store.get(root.child(b"snapshot")) == foreign.canonical
