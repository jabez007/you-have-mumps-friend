# SPDX-License-Identifier: GPL-3.0-or-later
"""Build/install the independent consumer in isolation, optionally upgrading 0.4.0."""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

# Historical prepared/qualified milestone commit, not a published release artifact.
HISTORICAL = "51e8456ae6762ac2ebc8db1a74b914c2cc3c280c"


def build(project, output, env):
    subprocess.run(
        ["uv", "build", "--wheel", "--project", str(project), "--out-dir", str(output)],
        env=env,
        cwd=project,
        check=True,
    )
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def legacy_metadata(content: str) -> str:
    project = tomllib.loads(content)["project"]
    pin = project["dependencies"][0]
    if not pin.startswith("mccann-hub-yhmf=="):
        raise ValueError("The consumer must declare its exact framework pin first")
    current = project["version"]
    legacy = current + ("." if "+" in current else "+") + "legacy04"
    return content.replace(pin, "mccann-hub-yhmf==0.4.0").replace(
        f'version = "{current}"', f'version = "{legacy}"'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--mode", choices=["fresh", "upgrade"], default="fresh")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    candidate = args.candidate.resolve(strict=True)
    env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    if args.offline:
        env["UV_OFFLINE"] = "true"
    with tempfile.TemporaryDirectory(prefix="yhmf-consumer-") as temporary:
        root = Path(temporary)
        app = root / "application"
        shutil.copytree(
            repo / "consumers/inventory-ledger",
            app,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
        consumer = build(app, root / "consumer-wheel", env)
        python = root / "venv/bin/python"
        subprocess.run(
            ["uv", "venv", str(root / "venv"), "--python", sys.executable],
            env=env,
            cwd=root,
            check=True,
        )
        native = ["--native"] if args.native else []
        snapshot = root / "legacy-snapshot.json"
        command = [str(python), "-I", "-m", "mccann_hub.inventory_ledger"]
        if args.mode == "upgrade":
            historical = root / "historical"
            historical.mkdir()
            archive = root / "historical.tar"
            with archive.open("wb") as output:
                subprocess.run(["git", "archive", HISTORICAL], cwd=repo, stdout=output, check=True)
            with tarfile.open(archive) as source:
                source.extractall(historical, filter="data")
            old = build(historical, root / "historical-wheel", env)
            metadata = app / "pyproject.toml"
            metadata.write_text(legacy_metadata(metadata.read_text()))
            old_consumer = build(app, root / "historical-consumer-wheel", env)
            subprocess.run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    str(old) + ("[native]" if args.native else ""),
                    str(old_consumer),
                ],
                cwd=root,
                env=env,
                check=True,
            )
            subprocess.run(
                ["uv", "pip", "check", "--python", str(python)], cwd=root, env=env, check=True
            )
            subprocess.run(
                command + ["seed-legacy", "--snapshot", str(snapshot)] + native,
                cwd=root,
                env=env,
                check=True,
            )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                str(candidate) + ("[native]" if args.native else ""),
                str(consumer),
            ],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "check", "--python", str(python)], cwd=root, env=env, check=True
        )
        subprocess.run(
            command + [args.mode, "--snapshot", str(snapshot)] + native,
            cwd=root,
            env=env,
            check=True,
        )
    print(f"Consumer qualification: {candidate.name}; mode={args.mode}; native={args.native}")


if __name__ == "__main__":
    main()
