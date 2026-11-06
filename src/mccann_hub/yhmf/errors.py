# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors raised before unsafe storage operations can proceed."""


class YHMFError(Exception):
    """Base exception for mapper errors."""


class StorageError(YHMFError, ValueError):
    """An invalid or unsupported storage representation."""


class SchemaError(YHMFError, ValueError):
    """A model declaration cannot be mapped safely."""


class SchemaMismatch(YHMFError):
    """The installed schema differs from the declared schema."""


class NotFound(YHMFError, LookupError):
    """The requested record does not exist."""


class AlreadyExists(YHMFError):
    """Insert would replace an existing record."""


class TransactionError(YHMFError):
    """A transaction handle expired or a nested callback was requested."""


class ReferenceViolation(YHMFError):
    """A reference target is missing or a referenced record cannot be deleted."""


class UniqueViolation(YHMFError):
    """An indexed value is already claimed by another field occurrence."""


class QueryError(YHMFError, ValueError):
    """A query has no supported, bounded execution plan."""


class MigrationError(YHMFError):
    """A migration cannot safely start, advance, or abort."""


class MaintenanceActive(MigrationError):
    """An active migration holds the database-wide mapper maintenance gate."""


class MutationError(YHMFError, ValueError):
    """A partial mutation cannot preserve declared validation or ownership."""
