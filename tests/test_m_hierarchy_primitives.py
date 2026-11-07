# SPDX-License-Identifier: GPL-3.0-or-later
"""Native bounded primitives retain Python's existing location/sum identities."""

import hashlib
import random
from decimal import Decimal

import pytest

from mccann_hub.yhmf.decimal_math import from_units, units
from mccann_hub.yhmf.errors import StorageError
from tests import test_m_writers

m_writer = test_m_writers.m_writer


def test_generated_primitives_have_no_unresolved_markers_or_unbounded_input(store):
    source = test_m_writers.setup(store).m_routine(test_m_writers.MRecord, name="sha256")
    assert "@SHA256@" not in source and "@SUMMATH@" not in source
    assert source.startswith("%YHMFentry ;")
    assert 'if $zlength(value)>4096 quit ""' in source
    assert max(map(len, source.splitlines())) < 1000


@pytest.mark.native
def test_native_m_sha256_vectors_and_padding_boundaries_match_hashlib(store, m_writer):
    _, run, _, _ = m_writer
    randomizer = random.Random(38)
    messages = ["", "abc", '["locations","s:A","bins","s:B","quantity"]', "雪\x00\x01"]
    messages += ["a" * length for length in (1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 511, 4096)]
    messages += [
        "".join(chr(randomizer.randrange(128)) for _ in range(length))
        for length in (7, 39, 57, 98, 333, 1024)
    ]
    data = {str(i): value for i, value in enumerate(messages)}
    expected = [hashlib.sha256(value.encode()).hexdigest() for value in messages]
    assert run("hashes", data).splitlines() == expected
    assert run("hashes", {"0": "a" * 4097}) == "invalid"


@pytest.mark.native
def test_native_signed_sum_arithmetic_and_encoding_match_python(store, m_writer):
    _, run, _, _ = m_writer
    randomizer = random.Random(138)
    pairs = [
        (0, 0),
        (10**18, -(10**18)),
        (10**39 - 1, 1),
        (-(10**39), -1),
        (10**40 - 1, 1),
        (-(10**40 - 1), -1),
        (1, -2),
        (-1, 2),
    ]
    pairs += [
        (randomizer.randrange(-(10**39), 10**39), randomizer.randrange(-(10**39), 10**39))
        for _ in range(80)
    ]
    pairs += [(10**power, 0) for power in range(40)]
    expected = []
    for left, right in pairs:
        total = left + right
        try:
            expected.append(f"{total}|{from_units(total)}")
        except StorageError:
            expected.append("invalid")
    assert (
        run(
            "arithmetic", {str(i): f"{left}|{right}" for i, (left, right) in enumerate(pairs)}
        ).splitlines()
        == expected
    )
    for invalid in ("", "-0", "00", "+1", "1.0", "1E1", "9" * 41):
        assert run("arithmetic", {"0": f"{invalid}|0"}) == "invalid"


@pytest.mark.native
def test_native_sum_sources_keep_the_exact_decimal_sum_domain(store, m_writer):
    _, run, _, _ = m_writer
    values = [
        Decimal("0E+100"),
        Decimal("-0.000"),
        Decimal("1E-18"),
        Decimal("-1.25"),
        Decimal("999999999999999999"),
        Decimal("1E+18"),
        Decimal("1E-19"),
        Decimal("1.234567890123456789"),
    ]
    expected = []
    for value in values:
        try:
            expected.append(str(units(value, source=True)))
        except StorageError:
            expected.append("invalid")
    assert (
        run("sources", {str(i): str(value) for i, value in enumerate(values)}).splitlines()
        == expected
    )
