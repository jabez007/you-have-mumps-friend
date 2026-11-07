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
