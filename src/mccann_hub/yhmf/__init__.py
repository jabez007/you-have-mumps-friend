# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit hierarchical persistence for YottaDB."""

from .index_rebuilds import IndexRebuildPlan
from .maintenance import MigrationStatus
from .migrations import MigrationPlan, MigrationStep
from .models import ChildField, Children, Field, Model, Reference
from .queries import IndexReport, QueryPlan
from .registry import Registry
from .repository import Database
from .schema_diff import SchemaChange, compare_schemas
from .schemas import SchemaInspection, SchemaSnapshot
from .storage import MemoryStore, NativeStore, Path, Store

__all__ = [
    "ChildField",
    "Children",
    "Database",
    "Field",
    "IndexReport",
    "IndexRebuildPlan",
    "MemoryStore",
    "MigrationPlan",
    "MigrationStatus",
    "MigrationStep",
    "Model",
    "NativeStore",
    "Path",
    "QueryPlan",
    "Registry",
    "Reference",
    "SchemaInspection",
    "SchemaChange",
    "SchemaSnapshot",
    "Store",
    "compare_schemas",
]
__version__ = "0.3.0rc1"
