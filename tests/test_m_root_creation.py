# SPDX-License-Identifier: GPL-3.0-or-later
"""Empty hierarchical roots use the existing atomic installation invariant."""

import subprocess
from pathlib import Path as FilePath

import pytest

from examples.inventory import Lot, Product, Warehouse
from mccann_hub.yhmf import Database, Field, MemoryStore, Model, Path, Reference, Registry
from tests import test_m_writers
from tests.adversarial_support import dump
from tests.test_m_writer_maintenance import replace_source
from tests.test_native_schema_safety import ready

m_writer = test_m_writers.m_writer


class NamedWarehouse(Warehouse):
    name: str = Field(unique=True, min_length=1)


@pytest.fixture
def root_writer(store, m_writer):
    _, run, routine, environment = m_writer
    db = Database(store, Registry(Product, Lot, NamedWarehouse))
    replace_source(routine, db.m_routine(NamedWarehouse, name="YHMFTAPI"))
    return db, run, routine, environment


def roots():
    return (Warehouse.__global__, "^YHMFSchema", "^YHMFIndex", "^YHMFUnique", "^YHMFRef")


@pytest.mark.native
def test_empty_hierarchical_root_creation_and_delete_match_python(store, root_writer):
    db, run, _, _ = root_writer
    assert run("insert", {"id": "A", "name": "Warehouse A"}) == "ok"
    expected = Database(MemoryStore(), Registry(Product, Lot, NamedWarehouse))
    expected.insert(NamedWarehouse(id="A", name="Warehouse A"))
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.get(NamedWarehouse, "A").locations == {}
    before = dump(store, roots())
    assert run("insert", {"id": "B", "name": "Warehouse A"}) == "unique"
    assert run("replace", {"id": "A", "name": "Changed"}) == "operation"
    assert dump(store, roots()) == before
    assert run("delete", {"id": "A"}) == "ok"
    expected.delete(NamedWarehouse, "A")
    assert dump(store, roots()) == dump(expected.store, roots())
    assert db.check_indexes(NamedWarehouse).issues == ()


@pytest.mark.native
@pytest.mark.parametrize("key", ["A", "OTHER"])
def test_root_initialization_never_adopts_raw_records(store, root_writer, key):
    db, run, _, _ = root_writer
    store.set(Path(Warehouse.__global__, (("s:" + key).encode(), b"raw")), b"unmanaged")
    before = dump(store, roots())
    assert run("insert", {"id": "A", "name": "Warehouse A"}) == "unmanaged"
    assert dump(store, roots()) == before
    assert not db.inspect_schema(NamedWarehouse).installed


@pytest.mark.native
@pytest.mark.parametrize("collision", [False, True])
def test_concurrent_first_root_inserts_install_one_trusted_schema(store, root_writer, collision):
    db, _, routine, environment = root_writer
    source = routine.read_text()
    marker = " ; YHMF writer checkpoint:"
    replace_source(
        routine,
        source.replace(
            marker, ' if $trestart=0 do\n . new reply\n . write "ready",!\n . read reply\n' + marker
        ),
    )
    workers = []
    try:
        for key in ("A", "B"):
            name = "same" if collision else key
            worker = subprocess.Popen(
                [
                    str(FilePath(environment["ydb_dist"]) / "yottadb"),
                    "-run",
                    "%XCMD",
                    f'do rootworker^YHMFWTEST("{key}","{name}")',
                ],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            workers.append(worker)
            ready(worker)
        statuses = []
        for worker in workers:
            output, error = worker.communicate("go\n", timeout=25)
            assert worker.returncode == 0 and not error, error
            statuses.append(output.strip())
        assert sorted(statuses) == (["ok", "unique"] if collision else ["ok", "ok"])
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    assert len(db.scan(NamedWarehouse)) == (1 if collision else 2)
    assert db.check_indexes(NamedWarehouse).issues == ()


@pytest.mark.native
def test_root_participants_rollback_initial_metadata_and_restart_safely(store, root_writer):
    db, run, routine, _ = root_writer
    before = dump(store, roots())
    assert run("rootcreatepair", {"name": "first"}) == "unique|0"
    assert dump(store, roots()) == before
    source = routine.read_text()
    marker = " ; YHMF reference changes complete:"
    replace_source(routine, source.replace(marker, " if $trestart=0 trestart\n" + marker))
    assert run("rootcreatepair", {"name": "second"}) == "ok|0"
    assert db.get(NamedWarehouse, "A").name == "first"
    assert db.get(NamedWarehouse, "B").name == "second"
    assert db.check_indexes(NamedWarehouse).issues == ()


@pytest.mark.native
def test_empty_root_deletion_respects_incoming_references(store, root_writer):
    db, run, _, _ = root_writer

    class Link(Model):
        __global__ = "^YHMFTestOrder"
        id: str = Field(primary_key=True)
        warehouse: Reference[NamedWarehouse]

    assert run("insert", {"id": "A", "name": "Warehouse A"}) == "ok"
    links = Database(store, Registry(Product, Lot, NamedWarehouse, Link))
    links.insert(Link(id="L", warehouse=Reference[NamedWarehouse](key="A")))
    before = dump(store, roots())
    assert run("delete", {"id": "A"}) == "references"
    assert dump(store, roots()) == before
    links.delete(Link, "L")
    assert run("delete", {"id": "A"}) == "ok"
