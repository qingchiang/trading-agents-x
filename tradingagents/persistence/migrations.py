"""Programmatic Alembic runner used by CLI, Web, worker, and tests."""

from __future__ import annotations

from importlib import resources

from alembic import command
from alembic.config import Config

from tradingagents.application.settings import AppSettings


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
        command.upgrade(config, revision)
