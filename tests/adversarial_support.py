# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent physical assertions for the bounded mutation reference model."""

import hashlib
import json
import os
from collections import Counter

from hypothesis import settings

from mccann_hub.yhmf import Path


def sequence_settings(native=False):
    stress = os.environ.get("YHMF_HYPOTHESIS_PROFILE") == "stress"
    return settings(
        max_examples=(30 if native else 150) if stress else (8 if native else 20),
        stateful_step_count=100 if stress else 30,
        deadline=None,
        print_blob=True,
    )


def dump(store, globals_):
    result = {}
    pending = [Path(name) for name in globals_]
    while pending:
        path = pending.pop()
        value = store.get(path)
        if value is not None:
            result[path] = value
        pending.extend(path.child(key) for key in store.children(path, limit=1000))
    return result


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def key(value):
    # The reference domain deliberately uses string keys; numeric-key properties
    # live in test_adversarial_properties. No mapper codec is used here.
    return "s:" + value


def scalar(field, value):
    if value is None:
        return "n:"
    if field == "quantity":
        return "d:0" if value == 0 else f"d:{value}E0"  # Generated integers 0..9.
    return ("r:s:" if field == "product" else "s:") + value


def index_entry(expected, root, field, location, value):
    token = b"v:" + json_bytes(scalar(field.split(".")[-1], value))
    subscripts = (b"YHMFTestWarehouse", field.encode(), token)
    encoded_location = json_bytes(location)
    digest = hashlib.sha256(encoded_location).hexdigest().encode()
    expected[Path("^YHMFIndex", subscripts + (key(root).encode(), digest))] = encoded_location
    if field.endswith("serial") and value is not None:
        claim = Path("^YHMFUnique", subscripts)
        assert claim not in expected, "Reference model contains duplicate unique claims"
        expected[claim] = json_bytes([key(root), location])


def audit(store, state, products):
    """Derive exact structures from plain values, including stale/extraneous entries."""
    expected, counts = {}, Counter()
    for root, warehouse in state.items():
        index_entry(expected, root, "name", ["name"], warehouse["name"])
        for bin_id, lots in warehouse["bins"].items():
            for child, value in lots.items():
                location = ["bins", key(bin_id), "lots", key(child)]
                for field in ("quantity", "serial", "product"):
                    index_entry(
                        expected, root, "bins.lots." + field, location + [field], value[field]
                    )
                reference_entry(expected, counts, root, location, value["product"])
    for target, count in counts.items():
        expected[Path("^YHMFRef", (b"YHMFTestProduct", key(target).encode()))] = str(count).encode()
    actual = dump(store, ("^YHMFIndex", "^YHMFUnique", "^YHMFRef"))
    for path in tuple(actual):
        if path.global_name == "^YHMFRef" and actual[path] == b"0":
            assert len(path.subscripts) == 2
            assert path.subscripts[1].decode()[2:] in products
            del actual[path]  # Empty count nodes are intentionally retained.
    assert actual == expected


def reference_entry(expected, counts, root, location, target):
    if target is None:
        return
    counts[target] += 1
    encoded = json_bytes(location + ["product"])
    digest = hashlib.sha256(encoded).hexdigest().encode()
    path = Path(
        "^YHMFRef",
        (
            b"YHMFTestProduct",
            key(target).encode(),
            b"YHMFTestWarehouse",
            key(root).encode(),
            digest,
        ),
    )
    expected[path] = encoded
