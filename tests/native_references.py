# SPDX-License-Identifier: GPL-3.0-or-later
"""Native reference race worker with a one-time transaction read barrier."""

import json
import sys

from mccann_hub.yhmf import Database, Field, Model, NativeStore, Path, Reference, Registry
from mccann_hub.yhmf.errors import ReferenceViolation


class Target(Model):
    __global__ = "^YHMFTestRefTarget"
    id: int = Field(primary_key=True)


class Source(Model):
    __global__ = "^YHMFTestRefSource"
    id: int = Field(primary_key=True)
    target: Reference[Target]


class RendezvousStore(NativeStore):
    def __init__(self, target_key):
        super().__init__()
        self.guard = Path("^YHMFRef", (b"YHMFTestRefTarget", str(target_key).encode("ascii")))
        self.waiting = True

    def get(self, path):
        value = super().get(path)
        if self.waiting and path == self.guard:
            # Test instrumentation only. Real retryable callbacks must not perform IO.
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Race controller did not release the worker")
        return value


def run(operation, target_key, source_key):
    db = Database(RendezvousStore(target_key), Registry(Target, Source))
    try:
        if operation == "create":
            db.insert(Source(id=source_key, target=Reference[Target](key=target_key)))
        elif operation == "delete_target":
            db.delete(Target, target_key)
        elif operation == "delete_source":
            db.delete(Source, source_key)
        else:
            raise ValueError("Unknown race operation")
        result = "committed"
    except ReferenceViolation:
        result = "restricted"
    print(json.dumps({"operation": operation, "result": result}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
