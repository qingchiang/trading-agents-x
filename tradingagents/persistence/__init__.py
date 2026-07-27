"""Database migration entry points."""

from .migrations import upgrade_database

__all__ = ["upgrade_database"]
