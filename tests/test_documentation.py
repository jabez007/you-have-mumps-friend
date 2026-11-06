# SPDX-License-Identifier: GPL-3.0-or-later
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from scripts.check_doc_examples import documents, run_document

ROOT = Path(__file__).parents[1]


def test_python_documentation_examples_execute_in_page_order():
    count = sum(run_document(path) for path in documents(ROOT))
    assert count >= 25


def test_doc_checker_preserves_source_lines_and_surfaces_failed_assertions(tmp_path):
    path = tmp_path / "example.md"
    path.write_text("# Example\n\n```python\nvalue = 1\nassert value == 2\n```\n")
    with pytest.raises(AssertionError) as error:
        run_document(path)
    assert error.traceback[-1].path == path
    assert error.traceback[-1].lineno == 4  # pytest uses zero-based line numbers.


def test_readme_links_render_on_pypi_and_repository_targets_exist():
    text = ROOT.joinpath("README.md").read_text()
    for target in re.findall(r"\]\(([^)]+)\)", text):
        assert target.startswith(("https://", "#")), target
        prefix = "https://github.com/jabez007/you-have-mumps-friend/blob/main/"
        if target.startswith(prefix):
            relative = unquote(urlparse(target.removeprefix(prefix)).path)
            assert ROOT.joinpath(relative).is_file(), target
