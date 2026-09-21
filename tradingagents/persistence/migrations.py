"""Programmatic Alembic runner used by CLI, Web, worker, and tests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from importlib import resources

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError

from tradingagents.configuration.settings import AppSettings


class IncompatibleDatabaseError(RuntimeError):
    """Raised when a database belongs to an unreleased, discarded schema."""


def upgrade_database(settings: AppSettings, revision: str = "head") -> None:
    database = settings.database_path
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            has_version = source.execute(
                "SELECT 1 FROM sqlite_master WHERE name='alembic_version'"
            ).fetchone()
            revisions = source.execute("SELECT version_num FROM alembic_version").fetchall() if has_version else []
            if revisions and revisions != [("0100_independent",)]:
                raise IncompatibleDatabaseError(
                    "This database requires offline conversion. Stop Web and worker, "
                    "upgrade to 0013_submission_identity using the old program if necessary, "
                    "then run tradingagents db migrate-current --source <0013.db> "
                    "--destination <new.db>. Keep the original for rollback."
                )
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
                "This database predates the independent migration baseline. "
                "Stop Web and worker. Upgrade older databases with the old program to "
                "0013_submission_identity, then run tradingagents db migrate-current "
                f"--source {database} --destination <new.db>. Keep the original for rollback."
            ) from exc
