# SPDX-License-Identifier: GPL-3.0-or-later
"""M Decimal bytes and exact comparisons agree with the Python persistence contract."""

from decimal import Decimal, localcontext
from pathlib import Path as FilePath

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import create_model

from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Registry
from mccann_hub.yhmf.decimal_math import from_units, units
from mccann_hub.yhmf.errors import SchemaError
from mccann_hub.yhmf.indexes import normalized
from mccann_hub.yhmf.schemas import schema_path
from tests import test_m_writer_transactions, test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_schema_contracts import seed_legacy

m_writer = test_m_writers.m_writer
participants = test_m_writer_transactions.participants


class Amount(Model):
    __global__ = "^YHMFTestProduct"
    id: str = Field(primary_key=True)
    value: Decimal = Field(unique=True)


class Limited(Amount):
    value: Decimal = Field(unique=True, ge=0, max_digits=18, decimal_places=6)


@pytest.fixture
def decimal_writer(store, m_writer):
    _, run, routine, _ = m_writer

    def bind(model=Amount):
        db = Database(store, Registry(model))
        replace_source(routine, db.m_routine(model, name="YHMFTAPI"))
        return db, run

    return bind


@pytest.mark.parametrize(
    "field",
    [
        Field(multiple_of=Decimal("0.25")),
        Field(ge=Decimal("1E-19")),
        Field(le=Decimal("1E+22")),
        Field(max_digits=32767),
    ],
)
def test_unsupported_decimal_constraints_fail_at_export_without_metadata_installation(store, field):
    model = create_model("Unsupported", __base__=Amount, value=(Decimal, field))
    db = Database(store, Registry(model))
    with pytest.raises(SchemaError, match="M writer|M Decimal"):
        db.m_routine(model, name="DecimalAPI")
    assert not db.inspect_schema(model).installed


@pytest.mark.native
@pytest.mark.parametrize(
    "text",
    ["12.50", "-0.00", "0E-999", "1E-18", "9999999999999999999999", "-123.456789012345678901"],
)
def test_m_decimal_record_index_and_claim_bytes_match_python_exactly(store, decimal_writer, text):
    db, run = decimal_writer()
    assert run("insert", {"id": "A", "value": text}) == "ok"
    expected = MemoryStore()
    Database(expected, Registry(Amount)).insert(Amount(id="A", value=Decimal(text)))
    roots = (Amount.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")
    assert dump(store, roots) == dump(expected, roots)
    assert db.get(Amount, "A").value.as_tuple() == Decimal(text).as_tuple()
    assert db.check_indexes(Amount).issues == ()


@pytest.mark.native
@pytest.mark.parametrize(
    "text",
    [
        "NaN",
        "Infinity",
        "+1",
        "01",
        "1.",
        "1e+1",
        "0.1E+2",
        "1.0E+1",
        "0E+0",
        "0E-6",
        "0.0000000",
        "1E-19",
        "1E+22",
    ],
)
def test_invalid_or_outside_wire_domain_decimal_inserts_leave_no_state(store, decimal_writer, text):
    db, run = decimal_writer()
    assert run("insert", {"id": "A", "value": text}) == "invalid"
    assert not db.inspect_schema(Amount).installed
    assert store.children(Path(Amount.__global__), limit=1) == ()
    assert store.children(Path("^YHMFIndex"), limit=1) == ()
    assert store.children(Path("^YHMFUnique"), limit=1) == ()


@pytest.mark.native
def test_scale_only_replacement_preserves_normalized_unique_identity(decimal_writer):
    db, run = decimal_writer()
    assert run("insert", {"id": "A", "value": "12.50"}) == "ok"
    assert run("replace", {"id": "A", "value": "12.5"}) == "ok"
    assert run("insert", {"id": "B", "value": "12.500"}) == "unique"
    assert db.get(Amount, "A").value.as_tuple() == Decimal("12.5").as_tuple()
    assert db.query(Amount).where(value=Decimal("12.5000")).all()[0].id == "A"
    assert db.check_indexes(Amount).issues == ()


@pytest.mark.native
def test_fixed_unit_bounds_compare_beyond_native_numeric_precision(decimal_writer):
    class Bounded(Amount):
        value: Decimal = Field(
            ge=Decimal("9999999999999999999998.000000000000000001"),
            lt=Decimal("9999999999999999999999"),
        )

    db, run = decimal_writer(Bounded)
    assert run("insert", {"id": "A", "value": "9999999999999999999998.000000000000000001"}) == "ok"
    assert run("replace", {"id": "A", "value": "9999999999999999999998"}) == "invalid"
    assert run("replace", {"id": "A", "value": "9999999999999999999999"}) == "invalid"
    assert db.get(Bounded, "A").value == Decimal("9999999999999999999998.000000000000000001")


@pytest.mark.native
@pytest.mark.parametrize("context_precision", [2, 28, 50])
def test_encoded_precision_limits_do_not_depend_on_context_or_normalization(
    decimal_writer, context_precision
):
    db, run = decimal_writer(Limited)
    assert run("insert", {"id": "A", "value": "12.50"}) == "ok"
    for text in ("-1", "0.0000001", "9999999999999", "0E-7", "0.1000000"):
        assert run("replace", {"id": "A", "value": text}) == "invalid"
    # Additional encoded scale is explicitly outside the M subset even when
    # Pydantic could accept it after its context-dependent normalize() relaxation.
    assert Limited(id="B", value=Decimal("0.1000000")).value == Decimal("0.1")
    with localcontext() as context:
        context.prec = context_precision
        assert db.get(Limited, "A").value == Decimal("12.50")
    assert db.check_indexes(Limited).issues == ()


@pytest.mark.native
@settings(
    max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(
    value=st.integers(min_value=-(10**40 - 1), max_value=10**40 - 1),
    extra_scale=st.integers(min_value=0, max_value=8),
)
def test_generated_m_decimal_parser_preserves_exact_units_and_normalization(
    decimal_writer, value, extra_scale
):
    _, run = decimal_writer()
    number = from_units(value)
    sign, digits, exponent = number.as_tuple()
    number = Decimal((sign, digits + (0,) * extra_scale, int(exponent) - extra_scale))
    text = str(number)
    assert units(number) == value
    assert run("decimal", {"value": text}) == "1|" + str(value) + "|" + normalized(number).decode()


@pytest.mark.native
def test_existing_python_decimal_outside_wire_domain_is_preserved(decimal_writer, store):
    db, run = decimal_writer()
    db.insert(Amount(id="A", value=Decimal("1E-19")))
    before = dump(store, (Amount.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique"))
    assert run("replace", {"id": "A", "value": "1"}) == "corrupt"
    assert db.get(Amount, "A").value == Decimal("1E-19")
    assert dump(store, (Amount.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique")) == before
    assert store.get(schema_path(Amount.__global__)) is not None


@pytest.mark.native
@pytest.mark.parametrize("mode", ["restart", "error"])
def test_decimal_claims_and_payloads_rollback_or_restart_together(
    store, m_writer, decimal_writer, mode
):
    db, run = decimal_writer()
    _, _, routine, _ = m_writer
    assert run("insert", {"id": "A", "value": "12.50"}) == "ok"
    injection = " if $trestart=0 trestart\n" if mode == "restart" else ' set $ecode=",U1005,"\n'
    replace_source(
        routine,
        routine.read_text().replace(
            " if install set ^YHMFSchema", injection + " if install set ^YHMFSchema"
        ),
    )
    if mode == "restart":
        assert run("replace", {"id": "A", "value": "15.500"}) == "ok"
        assert db.get(Amount, "A").value.as_tuple() == Decimal("15.500").as_tuple()
    else:
        assert run("caught", {"id": "B", "value": "15.500"}) == "error|0"
        assert [row.id for row in db.scan(Amount)] == ["A"]
        assert db.get(Amount, "A").value.as_tuple() == Decimal("12.50").as_tuple()
    assert db.check_indexes(Amount).issues == ()


@pytest.mark.native
def test_decimal_participant_composes_with_integer_model_and_aborts_failed_group(
    store, participants
):
    _, other, run, routine, _, _ = participants
    db = Database(store, Registry(Amount))
    replace_source(routine, db.m_routine(Amount, name="YHMFTAPI"))
    assert run("pair", {"id": "A", "value": "12.50"}) == "pending|1\nok|0"
    assert run("pair", {"id": "C", "value": "13.50", "second": "-1"}) == "invalid|0"
    assert [row.id for row in db.scan(Amount)] == ["A"]
    assert db.get(Amount, "A").value.as_tuple() == Decimal("12.50").as_tuple()
    assert len(other.scan(test_m_writer_transactions.Other)) == 1
    assert db.check_indexes(Amount).issues == ()


@pytest.mark.native
def test_legacy_decimal_binding_preserves_metadata_and_scale(store, m_writer, decimal_writer):
    db, run = decimal_writer()
    _, _, routine, _ = m_writer
    legacy = seed_legacy(store, Amount(id="A", value=Decimal("12.50")))
    replace_source(routine, db.m_routine(Amount, name="YHMFTAPI"))
    assert run("replace", {"id": "A", "value": "15.500"}) == "ok"
    assert db.get(Amount, "A").value.as_tuple() == Decimal("15.500").as_tuple()
    assert store.get(schema_path(Amount.__global__)) == legacy.fingerprint
    assert db.check_indexes(Amount).issues == ()


@pytest.mark.parametrize("name", ["insert", "prepare", "checkpoint", "decimal"])
def test_export_uses_private_initial_label_when_routine_name_matches_an_entry(name):
    db = Database(MemoryStore(), Registry(Amount))
    assert db.m_routine(Amount, name=name).startswith("%YHMFentry ; SPDX-License-Identifier")


@pytest.mark.native
@pytest.mark.parametrize("name", ["insert", "decimal", "schema", "recordmeta"])
def test_routine_names_matching_generated_labels_compile_and_write_native(store, m_writer, name):
    import subprocess

    db = Database(store, Registry(Amount))
    _, _, routine, environment = m_writer
    routine.with_name(name + ".m").write_text(db.m_routine(Amount, name=name))
    result = subprocess.run(
        [
            str(FilePath(environment["ydb_dist"]) / "yottadb"),
            "-run",
            "%XCMD",
            'set input("id")="A",input("value")="12.50" write $$insert^' + name + "(.input),!",
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    assert not result.stderr, result.stderr
    assert result.stdout.strip() == "ok"
    assert db.get(Amount, "A").value == Decimal("12.50")
