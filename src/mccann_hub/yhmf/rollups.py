# SPDX-License-Identifier: GPL-3.0-or-later
"""Root-owned exact Decimal sums and local deltas, independent of Decimal context."""

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from .codecs import decode_scalar, encode_scalar
from .decimal_math import from_units as _decimal
from .decimal_math import units as _units
from .errors import SchemaError, StorageError
from .models import Model
from .records import FieldValue, walk_values
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
        or node.model.model_fields[source.name].annotation is not Decimal
    ):
        raise SchemaError("sum_of requires a nonnullable owned Decimal below a child collection")
    if any(field.node is not None for field in node.fields) and not node.local_validation:
        raise SchemaError("Stored sum source owners with descendants require local validation")
    return source


def compile_fields(node: "NodeSpec") -> "NodeSpec":
    if not any(field.sum_of is not None for field in node.fields):
        return node
    if not node.local_validation:
        raise SchemaError("Stored sum roots require local validation without custom hooks")
    return replace(
        node,
        fields=tuple(
            replace(field, rollup_source=_source(node, field.sum_of))
            if field.sum_of is not None
            else field
            for field in node.fields
        ),
    )


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
    totals = _totals(fields, record)
    return record.model_copy(update=totals) if totals else record


def verify(fields: tuple["FieldSpec", ...], record: Model) -> None:
    if any(getattr(record, name) != value for name, value in _totals(fields, record).items()):
        raise StorageError("Stored sum disagrees with its owned inputs")


def _delta(field: "FieldSpec", values: tuple[FieldValue, ...]) -> int:
    return sum(
        _units(value, source=True) for current, _, value in values if current is field.rollup_source
    )


def augment(
    store: Store, changes: tuple["Change", ...], deleting: frozenset[Path]
) -> tuple[tuple["Change", ...], dict[Path, bytes]]:
    from .secondary import Change

    grouped: dict[Path, tuple[Change, dict[str, int]]] = {}
    for change in changes:
        if change.root in deleting:
            continue
        _, deltas = grouped.setdefault(change.root, (change, {}))
        for field in change.spec.fields:
            if field.rollup_source is not None:
                deltas[field.name] = (
                    deltas.get(field.name, 0)
                    + _delta(field, change.after)
                    - _delta(field, change.before)
                )
    additions, writes = [], {}
    for change, deltas in grouped.values():
        derived, nodes = _root_delta(store, change, deltas)
        if derived is not None:
            additions.append(derived)
        writes.update(nodes)
    return changes + tuple(additions), writes


def _root_delta(
    store: Store, change: "Change", deltas: dict[str, int]
) -> tuple["Change | None", dict[Path, bytes]]:
    from .secondary import Change

    before, after, writes = [], [], {}
    for field in change.spec.fields:
        delta = deltas.get(field.name, 0)
        if not delta:
            continue
        path = change.root.child(field.name.encode("ascii"))
        raw = store.get(path)
        if raw is None:
            raise StorageError("Stored sum is missing")
        old = decode_scalar(raw)
        assert field.adapter is not None
        old = field.adapter.validate_python(old)
        if encode_scalar(old) != raw:
            raise StorageError("Stored sum differs from its validated Decimal encoding")
        new = field.adapter.validate_python(_decimal(_units(old, source=False) + delta))
        before.append((field, (field.name.encode("ascii"),), old))
        after.append((field, (field.name.encode("ascii"),), new))
        writes[path] = encode_scalar(new)
    result = Change(change.spec, change.root, tuple(before), tuple(after)) if before else None
    return result, writes
