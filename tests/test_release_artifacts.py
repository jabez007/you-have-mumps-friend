# SPDX-License-Identifier: GPL-3.0-or-later
"""Release audits fail for local state, malformed source paths and missing M code."""

import io
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from scripts.check_artifacts import audit, files

ROOT = Path(__file__).parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


@pytest.mark.parametrize("name", ["candidate/.serena/cache.pkl", "candidate/.env"])
def test_developer_state_cannot_enter_a_distribution(tmp_path, name):
    path = tmp_path / "candidate.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="developer-machine"):
        audit(path, PROJECT, ROOT)


def test_source_archive_cannot_hide_traversal_in_its_root(tmp_path):
    path = tmp_path / "candidate.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo("../PKG-INFO")
        entry.size = 1
        archive.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe path"):
        files(path)


def test_missing_generated_m_template_blocks_artifact_qualification(tmp_path):
    path = tmp_path / "candidate.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mccann_hub/yhmf/__init__.py", '__version__ = "0.5.0"')
    with pytest.raises(ValueError, match="generated-M template"):
        audit(path, PROJECT, ROOT)
