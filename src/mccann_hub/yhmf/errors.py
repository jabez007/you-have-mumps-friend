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
