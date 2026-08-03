"""Programmatic Alembic runner used by CLI, Web, worker, and tests."""

from __future__ import annotations

from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

from tradingagents.application.settings import AppSettings


class IncompatibleDatabaseError(RuntimeError):
    """Raised when a database belongs to an unreleased, discarded schema."""


def upgrade_database(settings: AppSettings, revision: str = "head") -> None:
    settings.prepare_filesystem()
    migration_root = resources.files("tradingagents.persistence").joinpath(
        "alembic"
    )
    with resources.as_file(migration_root) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.set_main_option(
            "sqlalchemy.url",
            f"sqlite+pysqlite:///{settings.database_path}",
        )
        config.attributes["busy_timeout_ms"] = settings.busy_timeout_ms
        try:
            command.upgrade(config, revision)
        except CommandError as exc:
            message = str(exc)
            if (
                "Can't locate revision identified by" not in message
                and "No such revision" not in message
            ):
                raise
            database = settings.database_path
            raise IncompatibleDatabaseError(
                "The local database uses an incompatible unreleased schema. "
                "Stop Web and worker processes, remove "
                f"{database}, {database}-wal, and {database}-shm, then restart."
            ) from exc
