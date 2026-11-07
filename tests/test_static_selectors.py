# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real checker, including intentional errors outside its normal inputs."""

import subprocess
import sys


def test_strict_mypy_rejects_path_typos_and_wrong_reference_key_types(tmp_path):
    source = tmp_path / "bad_selectors.py"
    source.write_text("""from examples.inventory import MODELS, Product, Warehouse
from mccann_hub.yhmf import Database, MemoryStore, Registry
registry = Registry(*MODELS)
db = Database(MemoryStore(), registry)
product = registry.reference(Product, lambda p: p.id)
product(42)
db.path(Warehouse, lambda w: w.locatoins["DOCK"])
""")
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(source)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("error:") == 2, result.stdout
    assert "[arg-type]" in result.stdout and 'expected "str"' in result.stdout
    assert "[attr-defined]" in result.stdout and '"locatoins"' in result.stdout


def test_strict_mypy_checks_query_field_values_roots_and_nested_declarations(tmp_path):
    source = tmp_path / "query_fields.py"
    source.write_text("""from decimal import Decimal
from examples.inventory import MODELS, Product, Warehouse
from mccann_hub.yhmf import Database, MemoryStore, QueryField, Registry, each
db = Database(MemoryStore(), Registry(*MODELS))
sku: QueryField[Product, str] = db.field(Product, lambda p: p.sku)
quantity: QueryField[Warehouse, Decimal] = db.field(
    Warehouse, lambda w: each(each(each(w.locations).bins).balances).quantity)
db.query(Product).where(sku.eq("valid"))
db.query(Warehouse).where(quantity.eq(Decimal("1")))
sku.eq(42)
quantity.eq("1")
db.field(Product, lambda p: p.skuu)
db.field(Warehouse, lambda w: each(w.locatoins).id)
db.query(Warehouse).where(sku.eq("invalid root"))
db.query(Warehouse).order_by(sku)
""")
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-error-summary", str(source)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("error:") == 6, result.stdout
    assert result.stdout.count("[attr-defined]") == 2, result.stdout
    assert result.stdout.count("[arg-type]") == 4, result.stdout
