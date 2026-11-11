# SPDX-License-Identifier: GPL-3.0-or-later
"""Check release identity before qualification; manual dispatch never authorizes upload."""

import os
import subprocess
import tomllib
from pathlib import Path

from scripts.check_artifacts import package_version


def release_version(root, event, ref):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    source = (root / "src/mccann_hub/yhmf/__init__.py").read_text()
    if package_version(source) != version:
        raise ValueError("Python and project versions differ")
    if event == "workflow_dispatch":
        return version
    if event != "push" or ref != f"refs/tags/v{version}":
        raise ValueError("Production publication requires an exact v<version> tag push")
    headings = {line.strip() for line in (root / "CHANGELOG.md").read_text().splitlines()}
    if f"## {version}" not in headings:
        raise ValueError(f"Missing changelog section: ## {version}")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if ancestry.returncode:
        raise ValueError("Release commit must be reachable from fetched origin/main")
    return version


def main():
    version = release_version(
        Path(__file__).resolve().parents[1],
        os.environ["GITHUB_EVENT_NAME"],
        os.environ["GITHUB_REF"],
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"version={version}\n")
    print(f"Package version: {version}")


if __name__ == "__main__":
    main()
