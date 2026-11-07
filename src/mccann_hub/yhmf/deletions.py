# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded inverse-policy closure followed by one ordinary secondary transition."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import indexes, mutations, occurrences, references
from .codecs import Key, decode_key
from .errors import (
    MaintenanceActive,
    MutationError,
    NotFound,
    ReferenceViolation,
    SchemaError,
    StorageError,
)
from .models import Model
from .owned import (
    OwnedPath,
    Owner,
    check_collection_length,
    resolve,
    resolve_collection,
)
from .record_budget import require_growth
from .records import metadata, node_nodes, walk_values
from .registry import FieldSpec, ModelSpec
from .storage import Path, subtree_nodes

if TYPE_CHECKING:
    from .repository import Database

MAX_OWNERS = 100
MAX_EDGES = 1000
MAX_NODES = 10_000


def _under(path: Path, ancestor: Path) -> bool:
    return (
        path.global_name == ancestor.global_name
        and path.subscripts[: len(ancestor.subscripts)] == ancestor.subscripts
    )


@dataclass(frozen=True)
class _Unit:
    spec: ModelSpec
    key: Key
    at: OwnedPath
    owner: Owner
    record: Model
    nodes: dict[Path, bytes]


@dataclass(frozen=True)
class _Incoming:
    spec: ModelSpec
    key: Key
    location: occurrences.Address
    owner: Path


class _Plan:
    def __init__(self, tx: "Database") -> None:
        self.tx = tx
        self.units: dict[Path, _Unit] = {}
        self.cache: dict[Path, tuple[Owner, Model, dict[Path, bytes]]] = {}
        self.nodes: set[Path] = set()
        self.edges: list[_Incoming] = []
        self.pending: list[_Unit] = []
        self.expanded: set[Path] = set()

    def covered(self, path: Path) -> bool:
        return any(_under(path, root) for root in self.units)

    def read(
        self, spec: ModelSpec, key: Key, at: OwnedPath, *, mutate: bool = False
    ) -> tuple[Owner, Model, dict[Path, bytes]]:
        owner = resolve(self.tx, spec, key, at, mutate=mutate)
        if owner.path not in self.cache:
            if len(self.cache) >= MAX_OWNERS:
                raise MutationError(
                    "Delete exceeds its 100 decoded owner bound; "
                    "unlink sources in separate transactions"
                )
            record = mutations.read_owner(self.tx.store, owner)
            nodes = node_nodes(owner.node.fields, owner.path, record)
            if at:
                nodes[owner.path] = b"o:1"
            else:
                nodes.update(
                    {owner.path.child(b"%m", name): value for name, value in metadata(spec).items()}
                )
            self.nodes.update(nodes)
            if len(self.nodes) > MAX_NODES:
                raise MutationError(
                    "Delete exceeds its 10000 decoded node bound; "
                    "unlink sources in separate transactions"
                )
            self.cache[owner.path] = owner, record, nodes
        return self.cache[owner.path]

    def add(self, spec: ModelSpec, key: Key, at: OwnedPath, path: Path) -> None:
        if self.covered(path):
            return
        owner, record, nodes = self.read(spec, key, at, mutate=True)
        if owner.path != path:
            raise StorageError("Cascade owner address disagrees with its declaration")
        self.units = {root: unit for root, unit in self.units.items() if not _under(root, path)}
        unit = _Unit(spec, key, at, owner, record, nodes)
        self.units[path] = unit
        if not at:
            self.pending.append(unit)

    def incoming(self, unit: _Unit) -> tuple[Path, ...]:
        root = references.reference_root(unit.owner.path)
        count = references.inbound_count(self.tx.store, root)
        if count > MAX_EDGES - len(self.edges):
            raise MutationError(
                "Delete exceeds its 1000 incoming edge bound; "
                "unlink sources in separate transactions"
            )
        if not count:
            return ()
        nodes = subtree_nodes(self.tx.store, root, limit=1 + 3 * count)
        entries = tuple(
            sorted((path for path in nodes if path != root), key=lambda path: path.subscripts)
        )
        if len(entries) != count or any(
            len(path.subscripts) != len(root.subscripts) + 3 for path in entries
        ):
            raise StorageError("Reverse-reference count and complete edges disagree")
        return entries

    def source(self, unit: _Unit, path: Path) -> _Incoming:
        try:
            name = "^" + path.subscripts[-3].decode("ascii")
        except UnicodeError:
            raise StorageError("Invalid reverse source global name") from None
        spec = self.tx.registry._spec_for_global(name)
        if spec is None:
            raise ReferenceViolation(
                "Register every exact incoming source declaration before deleting this target"
            )
        raw = self.tx.store.get(path)
        location = occurrences.address(spec, raw, path.subscripts[-1])
        if location.field.target is not unit.spec.model:
            raise StorageError("Reverse entry names another target declaration")
        key = decode_key(path.subscripts[-2])
        try:
            owner, record, _ = self.read(spec, key, location.at)
        except MaintenanceActive:
            raise ReferenceViolation(
                "Record is still referenced by a source under maintenance"
            ) from None
        except (NotFound, SchemaError):
            raise StorageError(
                "Reverse entry points to a missing or invalid source owner"
            ) from None
        occurrences.check_address_value(self.tx.store, spec, key, location, record)
        value = getattr(record, location.field.name)
        links = references.links_at(
            self.tx.registry, spec, spec.path(key), ((location.field, location.relative, value),)
        )
        expected = references.Link(unit.spec.model, unit.owner.path, path, raw or b"")
        if links != frozenset((expected,)):
            raise StorageError("Reverse entry disagrees with its exact source field")
        return _Incoming(spec, key, location, owner.path)

    def discover(self) -> None:
        while self.pending:
            unit = self.pending.pop()
            if unit.owner.path in self.expanded:
                continue
            self.expanded.add(unit.owner.path)
            for path in self.incoming(unit):
                edge = self.source(unit, path)
                self.edges.append(edge)
                if edge.location.field.on_delete == "cascade":
                    root = edge.spec.path(edge.key).child(*edge.location.keyed_relative)
                    self.add(edge.spec, edge.key, edge.location.keyed_at, root)

    def nulls(self) -> tuple[mutations.PreparedPatch, ...]:
        changes: dict[Path, tuple[_Incoming, dict[str, None]]] = {}
        for edge in self.edges:
            if self.covered(edge.owner):
                continue
            if edge.location.field.on_delete == "restrict":
                raise ReferenceViolation("Record is still referenced outside the deletion closure")
            if edge.location.field.on_delete != "set_null":
                raise StorageError("Cascade did not cover its source owner")
            _, values = changes.setdefault(edge.owner, (edge, {}))
            values[edge.location.field.name] = None
        return tuple(
            mutations.prepare_patch(self.tx, edge.spec, edge.key, edge.location.at, values)
            for edge, values in changes.values()
        )

    def collection_counts(self) -> dict[Path, int]:
        # Resolve each surviving collection once after parent-dominates-child
        # normalization; no intermediate length is presented to validators.
        removed: dict[Path, int] = {}
        owners: dict[Path, tuple[Owner, FieldSpec, int]] = {}
        for unit in self.units.values():
            if not unit.at:
                continue
            path = Path(unit.owner.path.global_name, unit.owner.path.subscripts[:-1])
            if path not in owners:
                owner, field, path, count = resolve_collection(
                    self.tx, unit.spec, unit.key, unit.at[:-1], mutate=True
                )
                owners[path] = owner, field, count
            removed[path] = removed.get(path, 0) + 1
        result = {}
        for path, number in removed.items():
            owner, field, count = owners[path]
            check_collection_length(owner, field, count - number)
            result[path] = count - number
        return result

    def apply(self) -> None:
        patches, counts = self.nulls(), self.collection_counts()
        units = tuple(self.units.values())
        old_links = frozenset(
            link
            for unit in units
            for link in references.links_at(
                self.tx.registry,
                unit.spec,
                unit.spec.path(unit.key),
                walk_values(unit.owner.node.fields, unit.record, unit.owner.relative),
            )
        )
        old_entries = frozenset(
            entry
            for unit in units
            for entry in indexes.entries_at(
                unit.spec,
                unit.spec.path(unit.key),
                walk_values(unit.owner.node.fields, unit.record, unit.owner.relative),
            )
        )
        old_links |= frozenset(
            link
            for patch in patches
            for link in references.links_at(self.tx.registry, patch.spec, patch.root, patch.before)
        )
        new_links = frozenset(
            link
            for patch in patches
            for link in references.links_at(self.tx.registry, patch.spec, patch.root, patch.after)
        )
        old_entries |= frozenset(
            entry
            for patch in patches
            for entry in indexes.entries_at(patch.spec, patch.root, patch.before)
        )
        new_entries = frozenset(
            entry
            for patch in patches
            for entry in indexes.entries_at(patch.spec, patch.root, patch.after)
        )
        roots = {unit.owner.path for unit in units if not unit.at}
        if any(link.target in roots for link in new_links):
            raise ReferenceViolation(
                "A surviving validator retained a reference to a deleted target"
            )
        require_growth(
            self.tx.store,
            [(patch.spec, patch.root, patch.growth) for patch in patches]
            + [(unit.spec, unit.spec.path(unit.key), -len(unit.nodes)) for unit in units],
        )
        self.tx._check_reference_targets(new_links)
        references.replace(self.tx.store, old_links, new_links)
        for root in roots:
            references.restrict_delete(self.tx.store, root)
        indexes.replace(self.tx.store, old_entries, new_entries)
        for patch in patches:
            patch.apply_nodes(self.tx.store)
        for unit in units:
            self.tx.store.delete_tree(unit.owner.path)
        for path, count in counts.items():
            self.tx.store.set(path, b"c:" + str(count).encode("ascii"))


def delete(tx: "Database", spec: ModelSpec, key: Key) -> None:
    plan = _Plan(tx)
    plan.add(spec, key, (), spec.path(key))
    plan.discover()
    plan.apply()
