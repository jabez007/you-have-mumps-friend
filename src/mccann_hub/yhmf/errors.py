# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors raised before unsafe storage operations can proceed."""


class YHMFError(Exception):
    """Base exception for mapper errors."""


class StorageError(YHMFError, ValueError):
    """An invalid or unsupported storage representation."""
