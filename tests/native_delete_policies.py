# SPDX-License-Identifier: GPL-3.0-or-later
"""Release policy writes in a known order after their native read sets are established."""

import sys

from deletion_models import declarations

from mccann_hub.yhmf import Database, NativeStore, Reference, Registry
from mccann_hub.yhmf.errors import NotFound, ReferenceViolation


def run(operation):
    target, source, _, _ = declarations()
    db = Database(NativeStore(), Registry(target, source))
    waiting = True

    def change(tx):
        nonlocal waiting
        # Reading a missing marker remains valid when a concurrent delete wins.
        # Native TP must discard this stale read set before committing a write.
        model, key = (target, "T") if operation == "delete_target" else (source, "S")
        tx.store.get(tx.registry.spec(model).path(key).child(b"%m", b"layout"))
        if waiting:
            waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Policy worker was not released")
        if operation == "delete_target":
            return "committed" if tx.delete(target, "T") else "absent"
        if operation == "delete_source":
            tx.delete(source, "S")
        elif operation == "link":
            tx.patch(source, "S", changes={"optional": Reference[target](key="T")})
        else:
            tx.patch(source, "S", at=("lines", "A"), changes={"link": None})
        return "committed"

    try:
        print(db.atomic(change), flush=True)
    except (NotFound, ReferenceViolation) as exc:
        print(type(exc).__name__, flush=True)


if __name__ == "__main__":
    run(sys.argv[1])
