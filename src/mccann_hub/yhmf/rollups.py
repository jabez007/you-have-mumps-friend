# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned exact Decimal sums and local deltas, independent of Decimal context."""

from collections.abc import Iterator
from dataclasses import dataclass, replace
from dataclasses import field as state_field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from .codecs import decode_scalar, encode_scalar
from .decimal_math import from_units as _decimal
from .decimal_math import units as _units
from .errors import SchemaError, StorageError
from .models import Model
from .records import walk_values
from .storage import Path, Store

if TYPE_CHECKING:
    from .registry import FieldSpec, NodeSpec
    from .secondary import Change


def _source(node: "NodeSpec", path: str) -> "FieldSpec":
    names, collections = path.split("."), 0
    for name in names[:-1]:
        if not node.local_validation:
            raise SchemaError("Stored sum ancestors require local validation without custom hooks")
        field = next((field for field in node.fields if field.name == name), None)
        if field is None or field.node is None:
            raise SchemaError("sum_of must follow declared ownership paths")
        collections += field.kind == "children"
        node = field.node
    source = next((field for field in node.fields if field.name == names[-1]), None)
    if (
        not collections
        or source is None
        or source.kind != "scalar"
        or source.sum_of is not None
        or node.model.model_fields[source.name].annotation is not Decimal
    ):
        raise SchemaError("sum_of requires a nonnullable owned Decimal below a child collection")
    if any(field.node is not None for field in node.fields) and not node.local_validation:
        raise SchemaError("Stored sum source owners with descendants require local validation")
    return source


def compile_fields(node: "NodeSpec") -> "NodeSpec":
    node = replace(
        node,
        fields=tuple(
            replace(field, node=compile_fields(field.node)) if field.node is not None else field
            for field in node.fields
        ),
    )
    if not any(_targets(node.fields)):
        return node
    if not node.local_validation:
        raise SchemaError("Stored sum ancestors require local validation without custom hooks")
    return replace(
        node,
        fields=tuple(
            replace(field, rollup_source=_source(node, field.sum_of))
            if field.sum_of is not None
            else field
            for field in node.fields
        ),
    )


def _targets(fields: tuple["FieldSpec", ...], depth: int = 0) -> Iterator[tuple["FieldSpec", int]]:
    for field in fields:
        if field.sum_of is not None:
            yield field, depth
        if field.node is not None:
            yield from _targets(field.node.fields, depth + (2 if field.kind == "children" else 1))


def _totals(fields: tuple["FieldSpec", ...], record: Model) -> dict[str, Decimal]:
    targets = tuple(field for field in fields if field.rollup_source is not None)
    if not targets:
        return {}
    sums = {id(field.rollup_source): 0 for field in targets}
    for field, _, value in walk_values(fields, record):
        if id(field) in sums:
            sums[id(field)] += _units(value, source=True)
    result = {}
    for field in targets:
        assert field.adapter is not None
        result[field.name] = field.adapter.validate_python(_decimal(sums[id(field.rollup_source)]))
    return result


def initialize[M: Model](fields: tuple["FieldSpec", ...], record: M) -> M:
    """Derive targets after normal full validation without rerunning leaf callbacks."""
    updates = {}
    for field, value in _owned(fields, record):
        assert field.node is not None
        updates[field.name] = (
            {key: initialize(field.node.fields, child) for key, child in value.items()}
            if field.kind == "children"
            else initialize(field.node.fields, value)
        )
    updates.update(_totals(fields, record))
    return record.model_copy(update=updates) if updates else record


def verify(fields: tuple["FieldSpec", ...], record: Model) -> None:
    if any(getattr(record, name) != value for name, value in _totals(fields, record).items()):
        raise StorageError("Stored sum disagrees with its owned inputs")
    for field, value in _owned(fields, record):
        assert field.node is not None
        for child in value.values() if field.kind == "children" else (value,):
            verify(field.node.fields, child)


def _owned(fields: tuple["FieldSpec", ...], record: Model) -> Iterator[tuple["FieldSpec", Any]]:
    for field in fields:
        if (
            field.node is not None
            and any(_targets(field.node.fields))
            and (value := getattr(record, field.name)) is not None
        ):
            yield field, value


@dataclass
class _Deltas:
    change: "Change"
    values: dict[tuple[bytes, ...], tuple["FieldSpec", int]] = state_field(default_factory=dict)
    before: set[tuple[bytes, ...]] = state_field(default_factory=set)
    after: set[tuple[bytes, ...]] = state_field(default_factory=set)

    def add(self, change: "Change") -> None:
        self.before.update(path for field, path, _ in change.before if field.sum_of is not None)
        self.after.update(path for field, path, _ in change.after if field.sum_of is not None)
        for target, depth in _targets(change.spec.fields):
            for values, sign in ((change.before, -1), (change.after, 1)):
                for field, path, value in values:
                    if field is target.rollup_source:
                        relative = path[:depth] + (target.name.encode("ascii"),)
                        previous = self.values.get(relative, (target, 0))[1]
                        self.values[relative] = target, previous + sign * _units(value, source=True)


def augment(
    store: Store, changes: tuple["Change", ...], deleting: frozenset[Path]
) -> tuple[tuple["Change", ...], dict[Path, bytes]]:
    grouped: dict[Path, _Deltas] = {}
    for change in changes:
        if change.root in deleting:
            continue
        grouped.setdefault(change.root, _Deltas(change)).add(change)
    additions, writes = [], {}
    for deltas in grouped.values():
        derived, nodes = _owner_deltas(store, deltas)
        if derived is not None:
            additions.append(derived)
        writes.update(nodes)
    return changes + tuple(additions), writes


def _owner_deltas(store: Store, deltas: _Deltas) -> tuple["Change | None", dict[Path, bytes]]:
    from .secondary import Change

    before, after, writes = [], [], {}
    for relative, (field, delta) in deltas.values.items():
        # Inserted owners already contain initialized totals; removed owners have
        # no surviving total. Their ordinary occurrences maintain total indexes.
        if not delta or relative in deltas.before ^ deltas.after:
            continue
        path = deltas.change.root.child(*relative)
        raw = store.get(path)
        if raw is None:
            raise StorageError("Stored sum is missing")
        old = decode_scalar(raw)
        assert field.adapter is not None
        old = field.adapter.validate_python(old)
        if encode_scalar(old) != raw:
            raise StorageError("Stored sum differs from its validated Decimal encoding")
        new = field.adapter.validate_python(_decimal(_units(old, source=False) + delta))
        before.append((field, relative, old))
        after.append((field, relative, new))
        writes[path] = encode_scalar(new)
    change = deltas.change
    result = Change(change.spec, change.root, tuple(before), tuple(after)) if before else None
    return result, writes
