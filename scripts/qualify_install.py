# SPDX-License-Identifier: GPL-3.0-or-later
"""Install one selected artifact outside the checkout and run installed-only smoke."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    artifact = args.artifact.resolve(strict=True)
    env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    with tempfile.TemporaryDirectory(prefix="yhmf-installed-") as temporary:
        root = Path(temporary)
        python = root / "venv/bin/python"
        subprocess.run(
            ["uv", "venv", str(root / "venv"), "--python", sys.executable],
            env=env,
            cwd=root,
            check=True,
        )
        requirement = str(artifact) + ("[native]" if args.native else "")
        command = ["uv", "pip", "install", "--python", str(python), requirement]
        if args.offline:
            command.append("--offline")
        subprocess.run(command, env=env, cwd=root, check=True)
        smoke = root / "smoke.py"
        shutil.copyfile(Path(__file__).with_name("smoke-wheel.py"), smoke)
        subprocess.run(
            [str(python), "-I", str(smoke)] + (["--native"] if args.native else []),
            env=env,
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "check", "--python", str(python)], env=env, cwd=root, check=True
        )
    print(f"Qualified isolated installation: {artifact.name}; native={args.native}")


if __name__ == "__main__":
    main()
