# SPDX-License-Identifier: GPL-3.0-or-later
"""A small storage seam for native globals and deterministic offline tests."""

import importlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol, cast

from .errors import StorageError

MAX_VALUE_BYTES = 32_766
_GLOBAL = re.compile(r"\^[A-Za-z%][A-Za-z0-9]{0,30}\Z")
_INTEGER = re.compile(rb"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")


@dataclass(frozen=True)
class Path:
    """Validated native path. Key-size checks use a conservative upper bound."""

    global_name: str
    subscripts: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        if not _GLOBAL.fullmatch(self.global_name):
            raise StorageError("Invalid global name")
        if self.global_name.startswith("^%Y"):
            raise StorageError("The ^%Y namespace is reserved by YottaDB")
        if not isinstance(self.subscripts, tuple) or len(self.subscripts) > 31:
            raise StorageError("A path must have at most 31 subscripts in a tuple")
        if any(not isinstance(s, bytes) or not s or b"\0" in s for s in self.subscripts):
            raise StorageError("Subscripts must be nonempty bytes without NUL")
        if len(self.global_name) + 2 + sum(2 * len(s) + 2 for s in self.subscripts) > 1019:
            raise StorageError("Conservative key-size limit exceeded")

    def child(self, *subscripts: bytes) -> "Path":
        return Path(self.global_name, self.subscripts + subscripts)


class Store(Protocol):
    """Operations used by the mapper. Native errors propagate to the caller."""

    def get(self, path: Path) -> bytes | None: ...
    def set(self, path: Path, value: bytes) -> None: ...
    def delete_tree(self, path: Path) -> None: ...
    def children(self, path: Path, *, limit: int, after: bytes = b"") -> tuple[bytes, ...]: ...
    def atomic[T](self, callback: Callable[[], T]) -> T: ...


def _check_value(value: bytes) -> None:
    if not isinstance(value, bytes) or len(value) > MAX_VALUE_BYTES:
        raise StorageError("Node values must be bytes of length at most 32766")


def _check_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 10_000:
        raise StorageError("Traversal limit must be between 1 and 10000")


def _collation(value: bytes) -> tuple[int, int | bytes]:
    if _INTEGER.fullmatch(value):
        return (0, int(value))
    return (1, value)


class MemoryStore:
    """Rollback-capable offline adapter. Does not simulate native conflicts."""

    def __init__(self) -> None:
        self._nodes: dict[Path, bytes] = {}
        self._lock = RLock()

    def get(self, path: Path) -> bytes | None:
        with self._lock:
            return self._nodes.get(path)

    def set(self, path: Path, value: bytes) -> None:
        _check_value(value)
        with self._lock:
            self._nodes[path] = value

    def delete_tree(self, path: Path) -> None:
        with self._lock:
            self._nodes = {
                p: v
                for p, v in self._nodes.items()
                if p.global_name != path.global_name
                or p.subscripts[: len(path.subscripts)] != path.subscripts
            }

    def children(self, path: Path, *, limit: int, after: bytes = b"") -> tuple[bytes, ...]:
        _check_limit(limit)
        depth = len(path.subscripts)
        with self._lock:
            keys = {
                p.subscripts[depth]
                for p in self._nodes
                if p.global_name == path.global_name
                and len(p.subscripts) > depth
                and p.subscripts[:depth] == path.subscripts
            }
        ordered = sorted(keys, key=_collation)
        return tuple(k for k in ordered if not after or _collation(k) > _collation(after))[:limit]

    def atomic[T](self, callback: Callable[[], T]) -> T:
        with self._lock:
            before = self._nodes.copy()
            try:
                return callback()
            except BaseException:
                self._nodes = before
                raise


class NativeStore:
    """YDBPython adapter. Configure a database before constructing this class."""

    def __init__(self) -> None:
        try:
            self._ydb = importlib.import_module("yottadb")
        except ImportError as exc:
            raise RuntimeError(
                "Install YottaDB and the native extra before using NativeStore"
            ) from exc

    def get(self, path: Path) -> bytes | None:
        return cast(bytes | None, self._ydb.get(path.global_name, path.subscripts))

    def set(self, path: Path, value: bytes) -> None:
        _check_value(value)
        self._ydb.set(path.global_name, path.subscripts, value)

    def delete_tree(self, path: Path) -> None:
        self._ydb.delete_tree(path.global_name, path.subscripts)

    def children(self, path: Path, *, limit: int, after: bytes = b"") -> tuple[bytes, ...]:
        _check_limit(limit)
        keys: list[bytes] = []
        current = after
        for _ in range(limit):
            try:
                current = self._ydb.subscript_next(path.global_name, path.subscripts + (current,))
            except self._ydb.YDBNodeEnd:
                break
            keys.append(current)
        return tuple(keys)

    def atomic[T](self, callback: Callable[[], T]) -> T:
        result: list[T] = []

        def attempt() -> int:
            result.clear()
            result.append(callback())
            return cast(int, self._ydb.YDB_OK)

        self._ydb.tp(attempt)
        return result[0]
