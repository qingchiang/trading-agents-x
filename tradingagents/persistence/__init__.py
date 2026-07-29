"""Database migration entry points."""

from .migrations import IncompatibleDatabaseError, upgrade_database

__all__ = ["IncompatibleDatabaseError", "upgrade_database"]
