# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent counter writers, including deliberately broad read handlers."""

import json
import sys

from mccann_hub.yhmf import Database, NativeStore, Registry
from tests.native_schema_safety import Counter, wait


def run(iterations, broad):
    db = Database(NativeStore(), Registry(Counter))
    caught = 0
    wait()  # Start all workers before opening any retryable transaction.

    def increment(tx):
        nonlocal caught
        if broad:
            try:
                value = tx.get(Counter, 1)
            except Exception:
                caught += 1
                value = Counter(id=1, count=0)
        else:
            value = tx.get(Counter, 1)
        value.count += 1
        tx.save(value)

    for _ in range(iterations):
        db.atomic(increment)
    print(json.dumps({"commits": iterations, "caught": caught}), flush=True)


if __name__ == "__main__":
    run(int(sys.argv[1]), sys.argv[2] == "broad")
