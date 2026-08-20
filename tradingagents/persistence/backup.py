"""SQLite backup operations that do not initialize or migrate the application."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tradingagents.application.settings import AppSettings


def backup_sqlite_database(settings: AppSettings, destination: Path) -> Path:
    """Copy the live SQLite database without running application migrations.

    This is deliberately a persistence-only seam for the pre-migration CLI
    command.  Opening the source with SQLite's online backup API preserves the
    schema and rows that exist at the time of the copy, including legacy rows
    that a later Alembic migration may intentionally discard.
    """
    settings.prepare_filesystem()
    destination = destination.expanduser().resolve()
    if destination == settings.database_path.resolve():
        raise ValueError("backup destination must differ from the live database")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(
        settings.database_path,
        timeout=settings.busy_timeout_ms / 1000,
    )
    target = sqlite3.connect(
        destination,
        timeout=settings.busy_timeout_ms / 1000,
    )
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return destination
