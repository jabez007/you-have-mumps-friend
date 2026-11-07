# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
from pathlib import Path as FilePath

import pytest
from pydantic import ConfigDict, ValidationError, model_validator

from mccann_hub.yhmf import (
    Database,
    Field,
    GlobalView,
    MappedField,
    Model,
    NativeStore,
    Path,
    Registry,
)
from mccann_hub.yhmf.errors import NotFound, SchemaError, StorageError
from mccann_hub.yhmf.storage import subtree_nodes
from tests.test_partial_mutations import ObservedStore


class ForeignPart(Model):
    __global__ = "^YHMFTestStorage"
    id: int = Field(primary_key=True)
    name: str
    quantity: int = Field(ge=0)
    note: str = "Absent note"


def text(raw):
    return raw.decode("utf-8")


def integer(raw):
    return int(raw.decode("ascii"))


def view(store, model=ForeignPart):
    return GlobalView(
        store,
        model,
        root=Path(model.__global__, (b"foreign",)),
        fields={
            "name": MappedField((b"N",), text),
            "quantity": MappedField((b"Q",), integer),
            "note": MappedField((b"Note",), text),
        },
    )


def seed(store, key=b"2", name="Part", quantity=b"25"):
    root = Path(ForeignPart.__global__, (b"foreign", key))
    store.set(root.child(b"N"), name.encode("utf-8"))
    store.set(root.child(b"Q"), quantity)
    store.set(root.child(b"unrelated"), b"upstream-owned")


def test_foreign_projection_reads_native_nodes_without_adoption_or_rewrites(store, read_only_store):
    seed(store)
    before = subtree_nodes(store, Path(ForeignPart.__global__), limit=100)
    projected = view(read_only_store)
    assert projected.get(2) == ForeignPart(id=2, name="Part", quantity=25)
    assert projected.scan() == [projected.get(2)]
    assert not Database(store, Registry(ForeignPart)).inspect_schema(ForeignPart).installed
    assert subtree_nodes(store, Path(ForeignPart.__global__), limit=100) == before


def test_native_numeric_order_and_typed_key_cursors_are_bounded(store):
    for key in [b"10", b"2", b"1"]:
        seed(store, key)
    projected = view(store)
    assert [part.id for part in projected.scan(limit=2)] == [1, 2]
    assert [part.id for part in projected.scan(limit=2, after=2)] == [10]
    assert projected.scan(after=10) == []
    with pytest.raises(SchemaError, match="Key type"):
        projected.get("2")
    with pytest.raises(SchemaError, match="Key type"):
        projected.scan(after=True)
    with pytest.raises(NotFound):
        projected.get(99)


def test_string_key_projection_follows_native_string_numeric_aliases(store):
    class Named(ForeignPart):
        id: str = Field(primary_key=True)

    seed(store, b"2")
    seed(store, "雪".encode(), name="雪人")
    projected = view(store, Named)
    assert projected.get("2").id == "2"
    assert projected.get("雪").name == "雪人"
    assert [part.id for part in projected.scan()] == ["2", "雪"]


@pytest.mark.parametrize("limit", [0, 1001, True])
def test_foreign_scan_limits_remain_explicit(store, limit):
    with pytest.raises(ValueError, match="limits"):
        view(store).scan(limit=limit)


@pytest.mark.parametrize("raw", [b"01", b"+2", b"1.0", b"s:2", b"9" * 19])
def test_noncanonical_integer_keys_fail_instead_of_aliasing_foreign_records(store, raw):
    seed(store, raw)
    with pytest.raises(StorageError, match="key"):
        view(store).scan()


def test_piece_mappings_share_one_raw_read_and_run_complete_model_validation(store):
    class Packed(Model):
        __global__ = "^YHMFTestStorage"
        id: int = Field(primary_key=True)
        low: int
        high: int

        @model_validator(mode="after")
        def valid_range(self):
            if self.low > self.high:
                raise ValueError("Invalid range")
            return self

    observed = ObservedStore(store)
    packed = GlobalView(
        observed,
        Packed,
        fields={
            "low": MappedField((), lambda raw: int(raw.split(b"|")[0])),
            "high": MappedField((), lambda raw: int(raw.split(b"|")[1])),
        },
    )
    root = Path(Packed.__global__, (b"1",))
    store.set(root, b"2|8")
    assert packed.get(1).high == 8
    # One existence read and one shared raw-value read; no duplicate decoder fetch.
    assert sum(operation == "get" and path == root for operation, path in observed.operations) == 2
    store.set(root, b"9|8")
    with pytest.raises(ValidationError, match="Invalid range"):
        packed.get(1)


def test_decode_failures_and_missing_required_fields_are_clear(store):
    seed(store, quantity=b"bad")
    with pytest.raises(StorageError, match="mapped field 'quantity'"):
        view(store).get(2)
    seed(store, quantity=b"-1")
    with pytest.raises(ValidationError):
        view(store).get(2)
    store.delete_tree(Path(ForeignPart.__global__, (b"foreign", b"2", b"Q")))
    with pytest.raises(ValidationError, match="quantity"):
        view(store).get(2)


def test_projection_refuses_primary_key_changes_from_normalization(store):
    class Normalized(ForeignPart):
        model_config = ConfigDict(str_to_lower=True)
        id: str = Field(primary_key=True)

    seed(store, b"UPPER")
    with pytest.raises(StorageError, match="primary key"):
        view(store, Normalized).get("UPPER")


def test_mapping_configuration_rejects_unknown_missing_or_invalid_paths(store):
    with pytest.raises(SchemaError, match="each non-key"):
        GlobalView(store, ForeignPart, fields={})
    with pytest.raises(SchemaError, match="declared global"):
        GlobalView(store, ForeignPart, fields={}, root=Path("^Other"))
    with pytest.raises(SchemaError, match="tuple path"):
        MappedField([b"N"], text)
    with pytest.raises(StorageError):
        MappedField((b"bad\0",), text)

    class Indexed(ForeignPart):
        name: str = Field(index=True)

    with pytest.raises(SchemaError, match="without declared indexes"):
        view(store, Indexed)


@pytest.mark.native
def test_plain_m_global_can_be_projected_without_rewriting_any_source_byte(store, tmp_path):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires real M execution")
    (tmp_path / "YHMFLEGACY.m").write_text(
        "YHMFLEGACY ; SPDX-License-Identifier: GPL-3.0-or-later\n"
        " tstart ():serial\n"
        ' set ^YHMFTestStorage("foreign",2,"N")="Plain M part"\n'
        ' set ^YHMFTestStorage("foreign",2,"Q")=25\n'
        ' set ^YHMFTestStorage("foreign",10,"N")="Second part"\n'
        ' set ^YHMFTestStorage("foreign",10,"Q")=50\n'
        " tcommit\n quit\n"
    )
    environment = dict(os.environ)
    environment["ydb_routines"] = str(tmp_path) + " " + environment["ydb_routines"]
    result = subprocess.run(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "YHMFLEGACY"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    assert not result.stderr
    before = subtree_nodes(store, Path(ForeignPart.__global__), limit=100)
    assert [(part.id, part.name, part.quantity) for part in view(store).scan()] == [
        (2, "Plain M part", 25),
        (10, "Second part", 50),
    ]
    assert subtree_nodes(store, Path(ForeignPart.__global__), limit=100) == before
    assert not Database(store, Registry(ForeignPart)).inspect_schema(ForeignPart).installed
