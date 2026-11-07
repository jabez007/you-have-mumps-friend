# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path as FilePath

import pytest

from mccann_hub.yhmf import NativeStore, Path
from mccann_hub.yhmf.codecs import canonical_json, decode_scalar, encode_scalar
from mccann_hub.yhmf.indexes import value_key

FIXTURE = FilePath(__file__).with_name("fixtures") / "m-layout1-read.json"


def test_m_reader_goldens_are_canonical_mapper_values():
    for case in json.loads(FIXTURE.read_text()):
        raw = case["encoded"].encode("utf-8")
        assert encode_scalar(decode_scalar(raw)) == raw
        assert raw[2:].decode("utf-8") == case["payload"]
        assert (raw == b"n:") is case["null"]


@pytest.mark.native
def test_plain_m_layout_semantics_and_golden_reader(store, tmp_path, capsys):
    if not isinstance(store, NativeStore):
        pytest.skip("Requires real M execution")
    cases = json.loads(FIXTURE.read_text())
    root = Path("^YHMFTestStorage")
    for i, case in enumerate(cases, 1):
        raw = case["encoded"].encode("utf-8")
        store.set(root.child(b"golden", str(i).encode()), raw)
        store.set(root.child(b"tags", str(i).encode()), raw[:1])
    values = {
        "decimal": Decimal("12.50"),
        "precise": Decimal("0.123456789012345678901234567890"),
        "large": 1234567890123456789012345678901234567890,
        "bool": True,
        "integer": 1,
        "null": None,
        "empty": "",
    }
    for name, value in values.items():
        tagged = encode_scalar(value)
        store.set(root.child(b"tagged", name.encode()), tagged)
        store.set(root.child(b"plain", name.encode()), tagged[2:])
    for value in (2, 10):
        store.set(root.child(b"numeric_keys", str(value).encode()), b"1")
        store.set(root.child(b"string_keys", f"s:{value}".encode()), b"1")
        store.set(root.child(b"index_tokens", value_key(value)), b"1")
    project = FilePath(__file__).resolve().parents[1]
    for source in (project / "examples/m/YHMFREAD.m", project / "tests/m/YHMFVALUES.m"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    environment = dict(os.environ)
    environment["ydb_routines"] = str(tmp_path) + " " + environment["ydb_routines"]
    result = subprocess.run(
        [str(FilePath(environment["ydb_dist"]) / "yottadb"), "-run", "YHMFVALUES"],
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    lines = result.stdout.splitlines()
    assert lines[: len(cases)] == [
        f"golden|{i}|{int(case['null'])}|{case['payload']}" for i, case in enumerate(cases, 1)
    ]
    report = {}
    for line in lines[len(cases) :]:
        name, value = line.split("|", 1)
        report.setdefault(name, []).append(value)
    assert report["tagged_add"] == ["1"]
    assert report["payload_add"] == report["plain_add"] == ["13.5"]
    assert Decimal(report["precise_arithmetic"][0]) != values["precise"]
    assert report["precise_payload"] == [str(values["precise"])]
    assert Decimal(report["large_arithmetic"][0]) != values["large"] + 1
    assert report["large_payload"] == [str(values["large"])]
    assert report["plain_bool_int_equal"] == ["1"] and report["tagged_bool_int_equal"] == ["0"]
    assert report["null_empty_equal"] == ["0"]
    assert report["numeric_key"] == ["2", "10"]
    assert report["string_key"] == ["s:10", "s:2"]
    assert report["index_token"] == ['v:"i:10"', 'v:"i:2"']
    assert report["native_string_numeric_key_collision"] == ["string"]
    with capsys.disabled():
        print(
            "YHMF_VALUE_SEMANTICS "
            + canonical_json(
                {"journaling": os.environ.get("YHMF_JOURNALING") == "1", "observations": report}
            ).decode("ascii")
        )
