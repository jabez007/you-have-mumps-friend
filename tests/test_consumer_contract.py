# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the independent fixture on the documented artifact/public API boundary."""

import ast
import tomllib
from pathlib import Path

import pytest

from scripts.qualify_consumer import legacy_metadata

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


@pytest.mark.parametrize("candidate", ["0.5.0", "0.6.0rc1"])
def test_historical_consumer_binding_follows_the_selected_candidate(candidate):
    content = (
        (CONSUMER / "pyproject.toml")
        .read_text()
        .replace("mccann-hub-yhmf==0.5.0", f"mccann-hub-yhmf=={candidate}")
    )
    historical = tomllib.loads(legacy_metadata(content))["project"]
    assert historical["dependencies"] == ["mccann-hub-yhmf==0.4.0"]
    assert historical["version"] == "0.1.0+legacy04"
