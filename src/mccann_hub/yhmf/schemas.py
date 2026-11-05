# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical schema bytes and the installed-schema storage location."""

import json
from typing import Any

from .storage import Path


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def schema_path(global_name: str) -> Path:
    return Path("^YHMFSchema", (global_name[1:].encode("ascii"),))
