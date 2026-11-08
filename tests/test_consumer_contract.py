# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the independent fixture on the documented artifact/public API boundary."""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
CONSUMER = ROOT / "consumers/inventory-ledger"


def test_consumer_pins_the_exact_candidate_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    consumer = tomllib.loads((CONSUMER / "pyproject.toml").read_text())["project"]
    assert consumer["dependencies"] == [f"mccann-hub-yhmf=={project['version']}"]


def test_consumer_uses_only_documented_framework_imports():
    for path in (CONSUMER / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith("mccann_hub.yhmf"):
                    assert name in {"mccann_hub.yhmf", "mccann_hub.yhmf.errors"}, (path, name)
                assert not name.startswith(("examples", "tests", "scripts")), (path, name)
