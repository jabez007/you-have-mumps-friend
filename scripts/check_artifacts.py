# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit an exact source/wheel pair and write its qualification manifest and hashes."""

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import tarfile
import tomllib
import zipfile
from email import message_from_bytes
from importlib.metadata import version
from pathlib import Path, PurePosixPath

FORBIDDEN = {".serena", ".git", ".codex", ".agents", ".aws", ".env", ".venv", "__pycache__"}


def files(artifact):
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as archive:
            return {
                name: archive.read(name) for name in archive.namelist() if not name.endswith("/")
            }
    with tarfile.open(artifact) as archive:
        result = {}
        roots = set()
        for member in archive.getmembers():
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Source artifact contains an unsafe path")
            roots.add(path.parts[0])
            if len(roots) != 1:
                raise ValueError("Source artifact must have one root directory")
            if not member.isfile():
                raise ValueError("Source artifact contains a link or special file")
            stream = archive.extractfile(member)
            assert stream is not None
            result[member.name.partition("/")[2]] = stream.read()
        return result


def package_version(source):
    declarations = [
        node.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise ValueError("Expected exactly one package version")
    return ast.literal_eval(declarations[0])


def audit(artifact, project, root):
    content = files(artifact)
    for name in content:
        parts = PurePosixPath(name)
        if parts.is_absolute() or ".." in parts.parts or FORBIDDEN.intersection(parts.parts):
            raise ValueError("Artifact includes unsafe paths or developer-machine files")
    wheel = artifact.suffix == ".whl"
    prefix = "" if wheel else "src/"
    entry = prefix + "mccann_hub/yhmf/"
    expected = {p.name: p.read_bytes() for p in (root / "src/mccann_hub/yhmf").glob("*.template")}
    for name, raw in expected.items():
        if content.get(entry + name) != raw:
            raise ValueError(f"Missing or changed generated-M template: {name}")
    if entry + "py.typed" not in content or "mccann_hub/__init__.py" in content:
        raise ValueError("Typed namespace package contract is missing")
    if package_version(content[entry + "__init__.py"]) != project["version"]:
        raise ValueError("Python and project versions differ")
    audit_metadata(content, wheel, project, root)
    return {
        "file": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "bytes": artifact.stat().st_size,
        "entries": len(content),
    }


def audit_metadata(content, wheel, project, root):
    metadata = (
        [raw for name, raw in content.items() if name.endswith(".dist-info/METADATA")]
        if wheel
        else [content["PKG-INFO"]]
    )
    if len(metadata) != 1:
        raise ValueError("Expected exactly one distribution metadata document")
    message = message_from_bytes(metadata[0])
    for field, wanted in (
        ("Name", project["name"]),
        ("Version", project["version"]),
        ("License-Expression", "GPL-3.0-or-later"),
        ("Requires-Python", project["requires-python"]),
    ):
        if message[field] != wanted:
            raise ValueError(f"Artifact {field} differs from the declared contract")
    dependencies = message.get_all("Requires-Dist", [])
    if not any(value.startswith("pydantic<3,>=2.12") for value in dependencies) or not any(
        value.startswith("yottadb<3,>=2.0") and "native" in value for value in dependencies
    ):
        raise ValueError("Runtime or native-extra requirements are missing")
    licenses = [
        raw
        for name, raw in content.items()
        if name == "LICENSE" or name.endswith(".dist-info/licenses/LICENSE")
    ]
    if licenses != [(root / "LICENSE").read_bytes()]:
        raise ValueError("Artifact must contain the project's exact GPL license")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if args.expected_version and args.expected_version != project["version"]:
        parser.error("Expected version does not match pyproject.toml")
    wheels, sources = list(args.directory.glob("*.whl")), list(args.directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("Use a clean output directory containing exactly one wheel and sdist")
    rows = [audit(path, project, root) for path in wheels + sources]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines())
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=root).returncode == 0
    clean = clean and set(files(sources[0])).difference({"PKG-INFO"}).issubset(tracked)
    manifest = {
        "format_version": 1,
        "version": project["version"],
        "source_commit": commit,
        "source_tree_clean": clean,
        "qualification_run": os.environ.get("GITHUB_RUN_ID"),
        "qualification_status": "pending",
        "build_environment": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "uv": subprocess.check_output(["uv", "--version"], text=True).strip(),
        },
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "artifacts": rows,
    }
    (args.directory / "qualification.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.directory / "SHA256SUMS").write_text(
        "".join(f"{row['sha256']}  {row['file']}\n" for row in rows)
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
