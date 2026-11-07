# SPDX-License-Identifier: GPL-3.0-or-later
import os
import subprocess
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Annotated

import pytest
from pydantic import AfterValidator, ConfigDict, create_model

from mccann_hub.yhmf import (
    Database,
    Field,
    Model,
    NativeStore,
    Path,
    Reference,
    Registry,
)
from mccann_hub.yhmf.errors import SchemaError
from mccann_hub.yhmf.schemas import schema_path
from tests.test_native_schema_safety import ready
from tests.test_schema_contracts import seed_legacy


class MRecord(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True, min_length=1)
    sku: str = Field(unique=True, min_length=1)
    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=0, le=999)


def setup(store):
    return Database(store, Registry(MRecord))


def values(key="A", sku="S", name="Stock", quantity="1"):
    return {"id": key, "sku": sku, "name": name, "quantity": quantity}


def test_export_is_read_only_and_keeps_the_complete_installed_identity(read_only_store):
    db = setup(read_only_store)
    source = db.m_routine(MRecord, name="ProductAPI")
    assert source.startswith("ProductAPI ; SPDX-License-Identifier: GPL-3.0-or-later\n")
    assert not db.inspect_schema(MRecord).installed
    assert "@GLOBAL@" not in source and "@CONFIG@" not in source
    assert max(map(len, source.splitlines())) < 1000
    assert source == db.m_routine(MRecord, name="ProductAPI")


@pytest.mark.parametrize("name", ["", "1bad", "a-b", "a\nset ^X=1", "x" * 32, "雪", 1])
def test_export_rejects_invalid_routine_names(store, name):
    with pytest.raises(SchemaError, match="routine names"):
        setup(store).m_routine(MRecord, name=name)


@pytest.mark.parametrize(
    "kind",
    [
        "decimal_rule",
        "nullable",
        "pattern",
        "ordered",
        "hook",
        "normalization",
        "embedded",
        "many",
        "wrong_constraint",
    ],
)
def test_unsupported_writer_contracts_fail_at_export(store, kind):
    class Item(Model):
        id: str = Field(primary_key=True)

    annotations = {
        "decimal_rule": (Decimal, Field(multiple_of=Decimal("0.25"))),
        "nullable": (str | None, Field(default=None)),
        "pattern": (str, Field(pattern="^[A-Z]+$")),
        "ordered": (str, Field(ordered=True)),
        "hook": (Annotated[str, AfterValidator(lambda value: value)], Field()),
        "normalization": (str, Field()),
        "embedded": (Item, Field()),
        "many": (str, Field()),
        "wrong_constraint": (str, Field(ge=1)),
    }
    kwargs = {"value": annotations[kind]}
    if kind == "many":
        kwargs.update({f"extra{i}": (str, Field()) for i in range(16)})
    cls = create_model("UnsupportedM", __base__=MRecord, **kwargs)
    if kind == "normalization":
        cls.model_config = ConfigDict(**cls.model_config, str_to_lower=True)
        cls.model_rebuild(force=True)
    with pytest.raises(SchemaError, match="M writer"):
        Database(store, Registry(cls)).m_routine(cls, name="ProductAPI")


@pytest.fixture
def m_writer(store, tmp_path):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires real M execution")
    db = setup(store)
    routine = tmp_path / "YHMFTAPI.m"
    routine.write_text(db.m_routine(MRecord, name="YHMFTAPI"))
    driver = FilePath(__file__).with_name("m") / "YHMFWTEST.m"
    (tmp_path / driver.name).write_bytes(driver.read_bytes())
    environment = dict(os.environ)
    environment["ydb_routines"] = str(tmp_path) + " " + environment["ydb_routines"]
    command = str(FilePath(environment["ydb_dist"]) / "yottadb")
    root = Path("^YHMFTestStorage", (b"writerInput",))

    def run(operation, data):
        store.delete_tree(root)
        for field, value in data.items():
            store.set(root.child(field.encode("ascii")), value.encode("utf-8"))
        result = subprocess.run(
            [command, "-run", f"{operation}^YHMFWTEST"],
            env=environment,
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        assert not result.stderr, result.stderr
        return result.stdout.strip()

    return db, run, routine, environment


@pytest.mark.native
def test_generated_m_crud_matches_python_nodes_indexes_and_claims(store, m_writer):
    db, run, _, _ = m_writer
    assert run("insert", values(name='Stock "quoted" \\ \t\n\x01\x7f')) == "ok"
    assert db.get(MRecord, "A") == MRecord(
        id="A", sku="S", name='Stock "quoted" \\ \t\n\x01\x7f', quantity=1
    )
    assert db.check_indexes(MRecord).issues == ()
    assert run("insert", values()) == "exists"
    assert run("replace", values(sku="T", quantity="2")) == "ok"
    assert db.query(MRecord).where(sku="S").all() == []
    assert db.query(MRecord).where(sku="T").all()[0].quantity == 2
    db.patch(MRecord, "A", changes={"sku": "U"})
    assert run("replace", values(sku="V")) == "ok"
    assert db.check_indexes(MRecord).issues == ()
    assert run("delete", {"id": "A"}) == "ok"
    assert db.scan(MRecord) == []
    assert db.check_indexes(MRecord).issues == ()
    assert store.children(Path("^YHMFIndex", (b"YHMFTestProduct",)), limit=1) == ()
    assert store.children(Path("^YHMFUnique", (b"YHMFTestProduct",)), limit=1) == ()
    assert run("delete", {"id": "A"}) == "missing"


@pytest.mark.native
@pytest.mark.parametrize(
    "data",
    [
        values(name=""),
        values(name="雪"),
        values(quantity="-1"),
        values(quantity="1000"),
        values(quantity="01"),
        values(quantity="1.0"),
        values(quantity="1E0"),
        values(quantity="9" * 19),
        values(key="bad\0key"),
        {"id": "A"},
        dict(values(), unknown="x"),
        values(sku="x" * 1000),
    ],
)
def test_invalid_m_inserts_leave_no_schema_or_record_state(store, m_writer, data):
    db, run, _, _ = m_writer
    assert run("insert", data) in {"invalid", "invalid_key", "key_size"}
    assert not db.inspect_schema(MRecord).installed
    assert store.children(Path("^YHMFTestProduct"), limit=1) == ()
    assert store.children(Path("^YHMFIndex"), limit=1) == ()
    assert store.children(Path("^YHMFUnique"), limit=1) == ()


@pytest.mark.native
def test_failed_unique_replacement_rolls_back_released_old_claims(store, m_writer):
    db, run, _, _ = m_writer
    assert run("insert", values()) == "ok"
    db.insert(MRecord(id="B", sku="OTHER", name="Other", quantity=3))
    assert run("replace", values(sku="OTHER")) == "unique"
    assert db.get(MRecord, "A").sku == "S"
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
def test_m_initial_install_refuses_raw_data_at_a_different_key(store, m_writer):
    _, run, _, _ = m_writer
    raw = Path("^YHMFTestProduct", (b"raw",))
    store.set(raw, b"raw")
    assert run("insert", values()) == "unmanaged"
    assert store.get(raw) == b"raw"
    assert store.get(schema_path(MRecord.__global__)) is None


@pytest.mark.native
@pytest.mark.parametrize("damage", ["snapshot", "fingerprint", "extra", "record", "claim", "gate"])
def test_m_writes_refuse_corrupt_metadata_indexes_or_maintenance(store, m_writer, damage):
    db, run, _, _ = m_writer
    assert run("insert", values()) == "ok"
    schema = schema_path(MRecord.__global__)
    if damage == "snapshot":
        store.set(schema.child(b"snapshot"), b"{}")
    elif damage == "fingerprint":
        store.set(schema, b"0" * 64)
    elif damage == "extra":
        store.set(schema.child(b"unknown"), b"1")
    elif damage == "record":
        store.set(Path(MRecord.__global__, (b"s:A", b"%m", b"type")), b"Wrong")
    elif damage == "claim":
        store.delete_tree(Path("^YHMFUnique"))
    else:
        store.set(Path("^YHMFMigration", (b"gate",)), b"unknown")
    assert run("replace", values(sku="T")) in {"schema", "corrupt", "maintenance"}
    assert store.get(Path(MRecord.__global__, (b"s:A", b"sku"))) == b"s:S"
    store.delete_tree(Path("^YHMFMigration"))


@pytest.mark.native
def test_m_delete_conservatively_restricts_incoming_references(store, m_writer):
    db, run, _, _ = m_writer
    assert run("insert", values()) == "ok"

    class Link(Model):
        __global__ = "^YHMFTestOrder"
        id: int = Field(primary_key=True)
        product: Reference[MRecord] = Field(on_delete="cascade")

    linked = Database(store, Registry(MRecord, Link))
    linked.insert(Link(id=1, product=Reference[MRecord](key="A")))
    assert run("delete", {"id": "A"}) == "references"
    assert db.get(MRecord, "A").sku == "S"


@pytest.mark.native
def test_export_binds_legacy_metadata_without_rewriting_it(store, m_writer):
    db, run, routine, _ = m_writer
    record = MRecord(id="A", sku="S", name="Stock", quantity=1)
    legacy = seed_legacy(store, record)
    routine.write_text(db.m_routine(MRecord, name="YHMFTAPI"))
    assert run("replace", values(sku="T")) == "ok"
    assert store.get(schema_path(MRecord.__global__)) == legacy.fingerprint
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
def test_m_restarts_rebuild_old_index_state_and_rollback_partial_claim_writes(store, m_writer):
    db, run, routine, _ = m_writer
    assert run("insert", values()) == "ok"
    source = routine.read_text().replace(
        " if install set ^YHMFSchema", " if $trestart=0 trestart\n if install set ^YHMFSchema"
    )
    routine.write_text(source)
    routine.with_suffix(".o").unlink(missing_ok=True)
    assert run("replace", values(sku="T")) == "ok"
    assert db.get(MRecord, "A").sku == "T"
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
def test_concurrent_first_m_inserts_have_one_unique_winner(store, m_writer):
    db, _, routine, environment = m_writer
    source = routine.read_text().replace(
        " ; YHMF writer checkpoint:",
        ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n'
        ' . if reply\'="go" set $ecode=",U1004,"\n ; YHMF writer checkpoint:',
    )
    routine.write_text(source)
    workers = []
    command = str(FilePath(environment["ydb_dist"]) / "yottadb")
    try:
        for key in ["A", "B"]:
            worker = subprocess.Popen(
                [command, "-run", "%XCMD", f'do worker^YHMFWTEST("{key}","S")'],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(worker)
            try:
                ready(worker)
            except AssertionError:
                worker.kill()
                output, error = worker.communicate(timeout=5)
                raise AssertionError(
                    f"M worker failed before rendezvous: {output} {error}"
                ) from None
        statuses = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=20)
            assert worker.returncode == 0 and not error, error
            statuses.append(output.strip())
        assert statuses == ["ok", "unique"]
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    assert [r.id for r in db.scan(MRecord)] == ["A"]
    assert db.check_indexes(MRecord).issues == ()


@pytest.mark.native
def test_m_writer_rejects_nested_transaction_without_rolling_back_its_caller(store, m_writer):
    db, run, _, _ = m_writer
    assert run("nested", values()) == "nested|1"
    assert not db.inspect_schema(MRecord).installed


@pytest.mark.native
def test_unexpected_m_error_after_partial_index_writes_rolls_back_owned_transaction(
    store, m_writer
):
    db, run, routine, _ = m_writer
    routine.write_text(
        routine.read_text().replace(
            " if install set ^YHMFSchema", ' set $ecode=",U1005,"\n if install set ^YHMFSchema'
        )
    )
    assert run("caught", values()) == "error|0"
    assert not db.inspect_schema(MRecord).installed
    assert store.children(Path(MRecord.__global__), limit=1) == ()
    assert store.children(Path("^YHMFIndex"), limit=1) == ()
    assert store.children(Path("^YHMFUnique"), limit=1) == ()


@pytest.mark.native
def test_generated_integer_primary_keys_and_index_values_match_python(store, m_writer):
    _, run, routine, _ = m_writer

    class IntegerRecord(Model):
        __global__ = "^YHMFTestProduct"
        id: int = Field(primary_key=True)
        amount: int = Field(unique=True, gt=-100, lt=100)

    db = Database(store, Registry(IntegerRecord))
    routine.write_text(db.m_routine(IntegerRecord, name="YHMFTAPI"))
    assert run("insert", {"id": "-2", "amount": "-99"}) == "ok"
    assert db.get(IntegerRecord, -2).amount == -99
    assert db.check_indexes(IntegerRecord).issues == ()
    assert run("replace", {"id": "-2", "amount": "-100"}) == "invalid"
    assert run("replace", {"id": "-2", "amount": "99"}) == "ok"
    assert db.check_indexes(IntegerRecord).issues == ()
    assert run("delete", {"id": "-2"}) == "ok"
