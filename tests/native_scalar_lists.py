# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinate stale native list readers before releasing their writes."""

import json
import sys

from scalar_list_models import ListRecord

from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.errors import NotFound, UniqueViolation


def run(operation, key, field, raw):
    db = Database(NativeStore(), Registry(ListRecord))
    value = json.loads(raw)
    waiting = True

    def change(tx):
        nonlocal waiting
        tx.get(ListRecord, key)
        if waiting:
            waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("List worker was not released")
        if operation == "delete":
            tx.delete(ListRecord, key)
        else:
            tx.patch(ListRecord, key, changes={field: value})

    try:
        db.atomic(change)
        print("committed", flush=True)
    except (NotFound, UniqueViolation) as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(*sys.argv[1:])
