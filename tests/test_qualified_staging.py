# SPDX-License-Identifier: GPL-3.0-or-later
"""Publishing accepts only this run's unchanged qualified distribution pair."""

import hashlib
import json

import pytest

from scripts.check_artifacts import stage_qualified

VERSION = "0.5.0"
COMMIT = "a" * 40
RUN = "123"


@pytest.fixture
def candidate(tmp_path):
    directory = tmp_path / "candidate"
    directory.mkdir()
    rows = []
    for name, raw in (
        ("mccann_hub_yhmf-0.5.0-py3-none-any.whl", b"wheel\x00"),
        ("mccann_hub_yhmf-0.5.0.tar.gz", b"source\x00"),
    ):
        (directory / name).write_bytes(raw)
        rows.append(dict(file=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
    document = dict(
        format_version=1,
        version=VERSION,
        source_commit=COMMIT,
        qualification_run=RUN,
        qualification_status="passed",
        source_tree_clean=True,
        artifacts=rows,
    )
    (directory / "qualification.json").write_text(json.dumps(document))
    (directory / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['file']}\n" for r in rows))
    return directory, document, tmp_path / "upload"


def test_staging_preserves_qualified_bytes_and_passed_evidence(candidate):
    directory, document, output = candidate
    evidence = (directory / "qualification.json").read_bytes()
    stage_qualified(directory, output, VERSION, COMMIT, RUN)
    assert {p.name for p in output.iterdir()} == {r["file"] for r in document["artifacts"]}
    for row in document["artifacts"]:
        assert (output / row["file"]).read_bytes() == (directory / row["file"]).read_bytes()
    assert (directory / "qualification.json").read_bytes() == evidence


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("format_version", 2),
        ("qualification_status", "pending"),
        ("qualification_status", "failed"),
        ("source_tree_clean", False),
        ("source_tree_clean", 1),
        ("version", "0.6.0"),
        ("source_commit", "b" * 40),
        ("qualification_run", "122"),
    ],
)
def test_pending_or_foreign_evidence_cannot_be_staged(candidate, field, value):
    directory, document, output = candidate
    document[field] = value
    (directory / "qualification.json").write_text(json.dumps(document))
    with pytest.raises(ValueError):
        stage_qualified(directory, output, VERSION, COMMIT, RUN)
    assert not output.exists()


@pytest.mark.parametrize("damage", ["bytes", "checksums", "extra", "missing", "path", "duplicate"])
def test_changed_or_unexpected_distribution_sets_fail_before_staging(candidate, damage):
    directory, document, output = candidate
    wheel = directory / document["artifacts"][0]["file"]
    if damage == "bytes":
        wheel.write_bytes(b"changed")
    elif damage == "checksums":
        (directory / "SHA256SUMS").write_text("invalid\n")
    elif damage == "extra":
        (directory / "unqualified.whl").write_bytes(b"old")
    elif damage == "missing":
        wheel.unlink()
    elif damage == "path":
        document["artifacts"][0]["file"] = "../outside.whl"
    else:
        document["artifacts"].append(document["artifacts"][0])
    (directory / "qualification.json").write_text(json.dumps(document))
    with pytest.raises(ValueError):
        stage_qualified(directory, output, VERSION, COMMIT, RUN)
    assert not output.exists()


def test_staging_does_not_mix_files_into_an_existing_upload_directory(candidate):
    directory, _, output = candidate
    output.mkdir()
    (output / "old.whl").write_bytes(b"old")
    with pytest.raises(FileExistsError):
        stage_qualified(directory, output, VERSION, COMMIT, RUN)
    assert list(output.iterdir()) == [output / "old.whl"]
