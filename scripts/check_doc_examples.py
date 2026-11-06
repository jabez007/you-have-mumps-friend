# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute Python Markdown examples in order, with a fresh namespace per document."""

import argparse
from pathlib import Path


def run_document(path: Path) -> int:
    namespace = {"__name__": "__doc_example__", "__file__": str(path)}
    lines = path.read_text().splitlines(keepends=True)
    blocks, start, code = 0, None, []
    for number, line in enumerate(lines, 1):
        if start is None:
            if line.strip() == "```python":
                start, code = number + 1, []
        elif line.strip() == "```":
            source = "\n" * (start - 1) + "".join(code)
            exec(compile(source, str(path), "exec"), namespace)
            blocks += 1
            start = None
        else:
            code.append(line)
    if start is not None:
        raise ValueError(f"Unclosed Python fence: {path}:{start - 1}")
    return blocks


def documents(root: Path) -> list[Path]:
    return sorted(
        {
            root / "README.md",
            *root.joinpath("docs").rglob("*.md"),
            *root.joinpath("examples").rglob("README.md"),
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="*")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = args.files or documents(root)
    total = sum(run_document(path) for path in files)
    print(f"Executed {total} Python blocks across {len(files)} documents")


if __name__ == "__main__":
    main()
