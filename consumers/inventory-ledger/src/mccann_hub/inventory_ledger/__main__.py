# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent qualification using installed packages and documented public APIs."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path as FilePath

import mccann_hub.yhmf as framework
from mccann_hub.yhmf import Database, MemoryStore, NativeStore, Path, Reference, Registry, Store
from mccann_hub.yhmf.errors import AlreadyExists, ReferenceViolation

from . import Ledger, Location, LocationV1, Product, StockError, Warehouse, models

ROOTS = [
    "^ConsumerProduct",
    "^ConsumerWarehouse",
    "^ConsumerLot",
    "^ConsumerLocation",
    "^ConsumerMovement",
    "^YHMFSchema",
    "^YHMFIndex",
    "^YHMFUnique",
    "^YHMFRef",
    "^YHMFMigration",
]


def snapshot(store: Store) -> list[tuple[str, list[str], str]]:
    result: list[tuple[str, list[str], str]] = []

    def visit(path: Path) -> None:
        raw = store.get(path)
        if raw is not None:
            result.append((path.global_name, [part.hex() for part in path.subscripts], raw.hex()))
        children = store.children(path, limit=1000)
        assert len(children) < 1000, "Qualification fixture exceeded its explicit traversal bound"
        for key in children:
            visit(path.child(key))

    for name in ROOTS:
        visit(Path(name))
    return result


def m_interop(db: Database) -> None:
    with tempfile.TemporaryDirectory(prefix="consumer-m-") as temporary:
        root = FilePath(temporary)
        (root / "ConsumerAPI.m").write_text(db.m_routine(Product, name="ConsumerAPI"))
        (root / "ConsumerTest.m").write_text(
            "ConsumerTest\n quit\nrun\n new input\n"
            ' set input("id")="M",input("sku")="M-SKU",input("name")="M product"\n'
            " write $$insert^ConsumerAPI(.input),!\n quit\n"
        )
        env = dict(os.environ, ydb_routines=str(root) + " " + os.environ["ydb_routines"])
        result = subprocess.run(
            [str(FilePath(env["ydb_dist"]) / "yottadb"), "-run", "run^ConsumerTest"],
            env=env,
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        assert result.stdout.strip() == "ok" and not result.stderr, result.stderr
    assert db.get(Product, "M").name == "M product"
    assert db.query(Product).where(sku="M-SKU").all()[0].id == "M"
    assert db.store.get(Path(Product.__global__, (b"s:M", b"name"))) == b"s:M product"
    assert db.check_indexes(Product).issues == ()


def exercise(db: Database, native: bool) -> None:
    ledger = Ledger(db)
    ledger.receive("R", "LOT", "DOCK", Decimal(20))
    ledger.transfer("T", "LOT", "DOCK", "STORE", Decimal(7))
    ledger.adjust("A", "LOT", "STORE", Decimal("-0.5"), reason="Count")
    assert ledger.stock(location="DOCK")[0].balances["LOT"].quantity == 13
    assert ledger.stock(location="STORE")[0].balances["LOT"].quantity == Decimal("6.5")
    assert len(ledger.stock(product="P")) == len(ledger.stock(lot="LOT")) == 2
    assert {m.id for m in ledger.history("LOT")} >= {"R", "T", "A"}
    before = snapshot(db.store)
    try:
        ledger.receive("R", "LOT", "DOCK", Decimal(4))
    except AlreadyExists:
        pass
    else:
        raise AssertionError("Duplicate ledger ID was accepted")
    try:
        ledger.transfer("BAD", "LOT", "DOCK", "STORE", Decimal(1000))
    except StockError:
        pass
    else:
        raise AssertionError("Overspend was accepted")
    assert snapshot(db.store) == before
    try:
        db.delete(Warehouse, "W")
    except ReferenceViolation:
        pass
    else:
        raise AssertionError("Referenced warehouse deletion was accepted")
    assert (
        db.query(Location)
        .where(**{"balances.product": Reference[Product](key="P")})
        .explain()
        .strategy
        == "equality_index"
    )
    for model in models():
        assert db.check_indexes(model).issues == ()
    if native:
        m_interop(db)


def upgrade(store: Store) -> Database:
    db = Database(store, Registry(*models(LocationV1)))
    before = snapshot(store)
    assert db.get(LocationV1, "DOCK").balances["LOT"].quantity == 5
    assert db.inspect_schema(LocationV1).compatible
    assert db.check_indexes(LocationV1).issues == ()
    assert snapshot(store) == before, "Dependency upgrade silently changed persisted metadata"
    target = Database(store, Registry(*models()))
    plan = target.plan_migration(Location)
    assert plan.execution_supported
    job = target.begin_migration(LocationV1, Location, plan=plan)
    for _ in range(100):
        if job.phase == "complete":
            break
        target = Database(store, Registry(*models()))
        job = target.advance_migration(job.migration_id, LocationV1, Location, batch_size=1)
    assert job.phase == "complete"
    assert target.get(Location, "DOCK").description == ""
    assert target.get(Location, "DOCK").balances["LOT"].quantity == 5
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["fresh", "seed-legacy", "upgrade"])
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--snapshot", type=FilePath)
    args = parser.parse_args()
    assert FilePath(__file__).resolve().is_relative_to(FilePath(sys.prefix).resolve())
    assert FilePath(framework.__file__).resolve().is_relative_to(FilePath(sys.prefix).resolve())
    if args.native and os.environ.get("YHMF_TEST_NATIVE") != "1":
        parser.error("Use the disposable native harness for qualification")
    store = NativeStore() if args.native else MemoryStore()
    if args.mode == "seed-legacy":
        db = Database(store, Registry(*models(LocationV1)))
        ledger = Ledger(db, LocationV1)
        ledger.create()
        ledger.receive("LEGACY", "LOT", "DOCK", Decimal(5))
        if not args.native:
            assert args.snapshot is not None
            args.snapshot.write_text(json.dumps(snapshot(store)))
    else:
        if args.mode == "upgrade":
            if not args.native:
                assert args.snapshot is not None
                for name, parts, raw in json.loads(args.snapshot.read_text()):
                    store.set(
                        Path(name, tuple(bytes.fromhex(part) for part in parts)), bytes.fromhex(raw)
                    )
            db = upgrade(store)
            # Preserve old state while giving the workflow its ordinary initial stock.
            Ledger(db).adjust("RESET", "LOT", "DOCK", Decimal(-5), reason="Qualification")
        else:
            db = Database(store, Registry(*models()))
            Ledger(db).create()
        exercise(db, args.native)
    print(
        f"Independent ledger: {args.mode}; YHMF={version('mccann-hub-yhmf')}; "
        f"native={args.native}; passed"
    )


if __name__ == "__main__":
    main()
