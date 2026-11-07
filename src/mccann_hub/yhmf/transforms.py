# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit application-owned transform revisions for checkpointed schema changes."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import MigrationError
from .models import Model


@dataclass(frozen=True)
class Transform[S: Model, T: Model]:
    """A pure source-to-target function; name changes when its behavior changes."""

    name: str
    convert: Callable[[S], T] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}", self.name)
            or not callable(self.convert)
        ):
            raise MigrationError("Transform requires a stable ASCII revision name and a callable")
