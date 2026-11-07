# SPDX-License-Identifier: GPL-3.0-or-later
"""Transform worker retaining an old checkpoint while a competitor commits."""

import json
import sys

from transform_models import RELATED, Source, case

from mccann_hub.yhmf import Database, NativeStore, Registry
from mccann_hub.yhmf.maintenance import job_path


class RendezvousStore(NativeStore):
    def __init__(self, migration_id):
        super().__init__()
        self.guard, self.waiting = job_path(migration_id), True

    def get(self, path):
        value = super().get(path)
        if path == self.guard and self.waiting:
            self.waiting = False
            print("ready", flush=True)
            if sys.stdin.readline().strip() != "go":
                raise RuntimeError("Transform worker was not released")
        return value


def main(migration_id, kind):
    target, transform = case(kind)
    db = Database(RendezvousStore(migration_id), Registry(*RELATED, target))
    status = db.advance_migration(migration_id, Source, target, transform=transform, batch_size=1)
    print(json.dumps(status.to_dict()), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
