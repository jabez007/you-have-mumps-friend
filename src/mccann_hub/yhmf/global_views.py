# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit read-only model projections of foreign native global layouts."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .codecs import Key
from .errors import NotFound, SchemaError, StorageError
from .models import Model
from .queries import _limit
from .records import MAX_RECORD_NODES
from .registry import Registry
from .schemas import has_data
from .storage import MAX_VALUE_BYTES, Path, Store


@dataclass(frozen=True)
class MappedField:
    path: tuple[bytes, ...]
    decode: Callable[[bytes], Any]

    def __post_init__(self) -> None:
        if type(self.path) is not tuple or not callable(self.decode):
            raise SchemaError("Mapped fields require a tuple path and a callable decoder")
        Path("^Projection", self.path)


class GlobalView[M: Model]:
    """Validate foreign scalar records without claiming managed persistence invariants."""

    def __init__(
        self,
        store: Store,
        model: type[M],
        *,
        fields: Mapping[str, MappedField],
        root: Path | None = None,
    ) -> None:
        self._store, self._model, self._registry = store, model, Registry(model)
        spec = self._registry.spec(model)
        if len(spec.fields) > MAX_RECORD_NODES:
            raise SchemaError("Projection exceeds the record field budget")
        if any(field.kind != "scalar" or field.index for field in spec.fields):
            raise SchemaError("Global views require flat scalar models without declared indexes")
        self._root = root or Path(spec.global_name)
        if self._root.global_name != spec.global_name:
            raise SchemaError("Projection root must use the model's declared global")
        expected = {field.name for field in spec.fields} - {spec.key_field}
        self._fields = dict(fields)
        if self._fields.keys() != expected or not all(
            isinstance(field, MappedField) for field in self._fields.values()
        ):
            raise SchemaError("Map each non-key model field exactly once")
        for field in self._fields.values():
            self._root.child(b"k", *field.path)

    def _key(self, key: Key) -> bytes:
        spec = self._registry.spec(self._model)
        spec.path(key)  # Reuse exact declared key type and canonical key-size checks.
        return key.encode("utf-8") if isinstance(key, str) else str(key).encode("ascii")

    def _read(self, key: Key) -> M:
        spec = self._registry.spec(self._model)
        root = self._root.child(self._key(key))
        if not has_data(self._store, root):
            raise NotFound("Projected record does not exist")
        values: dict[str, Any] = {spec.key_field: key}
        cache: dict[Path, bytes | None] = {}
        for name, field in self._fields.items():
            path = root.child(*field.path)
            if path not in cache:
                cache[path] = self._store.get(path)
            raw = cache[path]
            if raw is not None:
                if len(raw) > MAX_VALUE_BYTES:
                    raise StorageError("Mapped field exceeds the node value limit")
                try:
                    values[name] = field.decode(raw)
                except (ValueError, ArithmeticError):
                    raise StorageError(f"Cannot decode mapped field {name!r}") from None
        result = self._model.model_validate(values)
        if getattr(result, spec.key_field) != key:
            raise StorageError("Projected primary key changed during model validation")
        return result

    def get(self, key: Key) -> M:
        return self._store.atomic(lambda: self._read(key))

    def scan(self, *, limit: int = 100, after: Key | None = None) -> list[M]:
        _limit(limit)
        cursor = b"" if after is None else self._key(after)

        def read() -> list[M]:
            spec = self._registry.spec(self._model)
            key_type = self._model.model_fields[spec.key_field].annotation
            result = []
            for raw in self._store.children(self._root, limit=limit, after=cursor):
                try:
                    key = raw.decode("utf-8") if key_type is str else int(raw.decode("ascii"))
                    if self._key(key) != raw:
                        raise ValueError
                except (ValueError, UnicodeError):
                    raise StorageError("Native key is incompatible with the projection") from None
                result.append(self._read(key))
            return result

        return self._store.atomic(read)
