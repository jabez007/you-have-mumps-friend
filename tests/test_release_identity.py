# SPDX-License-Identifier: GPL-3.0-or-later
"""Production release guards use committed versions, notes and real Git ancestry."""

import os
import subprocess

import pytest

from scripts.check_release import release_version


def git(root, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Release tests",
            "-c",
            "user.email=release-tests@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *arguments,
        ],
        cwd=root,
        env=environment,
        text=True,
    ).strip()


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.5.0"\n')
    package = tmp_path / "src/mccann_hub/yhmf"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.5.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n## 0.5.0\n")
    git(tmp_path, "init", "-q", "--initial-branch=main")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Release fixture")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def test_matching_tag_on_main_passes(project):
    assert release_version(project, "push", "refs/tags/v0.5.0") == "0.5.0"


@pytest.mark.parametrize(
    "event,ref",
    [
        ("push", "refs/tags/v0.4.0"),
        ("push", "refs/tags/0.5.0"),
        ("push", "refs/tags/v0.5.00"),
        ("push", "refs/heads/v0.5.0"),
        ("pull_request", "refs/tags/v0.5.0"),
    ],
)
def test_wrong_tag_or_event_fails(project, event, ref):
    with pytest.raises(ValueError, match="tag push"):
        release_version(project, event, ref)


@pytest.mark.parametrize(
    "event,ref", [("push", "refs/tags/v0.5.0"), ("workflow_dispatch", "refs/heads/main")]
)
def test_python_and_project_version_must_agree(project, event, ref):
    (project / "src/mccann_hub/yhmf/__init__.py").write_text('__version__ = "0.4.0"\n')
    with pytest.raises(ValueError, match="versions differ"):
        release_version(project, event, ref)


@pytest.mark.parametrize("heading", ["## Unreleased", "## 0.5.00", "### 0.5.0"])
def test_tag_requires_its_exact_release_notes_section(project, heading):
    (project / "CHANGELOG.md").write_text(heading + "\n")
    with pytest.raises(ValueError, match="changelog"):
        release_version(project, "push", "refs/tags/v0.5.0")


def test_tag_commit_outside_main_fails(project):
    git(project, "checkout", "-qb", "unmerged")
    (project / "unmerged.txt").write_text("New release work\n")
    git(project, "add", "unmerged.txt")
    git(project, "commit", "-qm", "Unmerged fixture")
    with pytest.raises(ValueError, match="origin/main"):
        release_version(project, "push", "refs/tags/v0.5.0")


def test_unfetched_main_ref_fails(project):
    git(project, "update-ref", "-d", "refs/remotes/origin/main")
    with pytest.raises(ValueError, match="origin/main"):
        release_version(project, "push", "refs/tags/v0.5.0")


def test_older_detached_commit_reachable_from_main_passes(project):
    release = git(project, "rev-parse", "HEAD")
    (project / "later.txt").write_text("Later work\n")
    git(project, "add", "later.txt")
    git(project, "commit", "-qm", "Later main fixture")
    git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(project, "checkout", "-q", "--detach", release)
    assert release_version(project, "push", "refs/tags/v0.5.0") == "0.5.0"


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.5.0"])
def test_manual_qualification_does_not_require_tag_or_final_notes(project, ref):
    (project / "CHANGELOG.md").write_text("## Unreleased\n")
    assert release_version(project, "workflow_dispatch", ref) == "0.5.0"


def test_prerelease_tag_matches_exact_pep440_version(project):
    (project / "pyproject.toml").write_text('[project]\nversion = "0.6.0rc1"\n')
    (project / "src/mccann_hub/yhmf/__init__.py").write_text('__version__ = "0.6.0rc1"\n')
    (project / "CHANGELOG.md").write_text("## Unreleased\n\n## 0.6.0rc1\n")
    git(project, "add", ".")
    git(project, "commit", "-qm", "Prerelease fixture")
    git(project, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert release_version(project, "push", "refs/tags/v0.6.0rc1") == "0.6.0rc1"
