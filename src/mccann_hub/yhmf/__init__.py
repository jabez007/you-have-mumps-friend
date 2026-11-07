# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit hierarchical persistence for YottaDB."""

from .global_views import GlobalView, MappedField
from .grouped_mutations import Patch
from .index_rebuilds import IndexRebuildPlan
from .maintenance import MigrationStatus
from .migrations import MigrationPlan, MigrationStep
from .models import ChildField, Children, Field, Model, Reference
from .occurrences import Occurrence
from .queries import IndexReport, PredicatePlan, QueryPlan
from .registry import Registry
from .repository import Database
from .schema_diff import SchemaChange, compare_schemas
from .schemas import SchemaInspection, SchemaSnapshot
from .selectors import FieldPredicate, ModelPath, QueryField, ReferenceFactory, each
from .storage import MemoryStore, NativeStore, Path, Store
from .transforms import Transform

__all__ = [
    "ChildField",
    "Children",
    "Database",
    "Field",
    "FieldPredicate",
    "GlobalView",
    "IndexReport",
    "IndexRebuildPlan",
    "MemoryStore",
    "MappedField",
    "MigrationPlan",
    "MigrationStatus",
    "MigrationStep",
    "Model",
    "ModelPath",
    "NativeStore",
    "Occurrence",
    "Patch",
    "Path",
    "PredicatePlan",
    "QueryPlan",
    "QueryField",
    "Registry",
    "Reference",
    "ReferenceFactory",
    "SchemaInspection",
    "SchemaChange",
    "SchemaSnapshot",
    "Store",
    "Transform",
    "compare_schemas",
    "each",
]
__version__ = "0.5.0"
