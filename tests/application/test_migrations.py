from __future__ import annotations

from sqlalchemy import inspect, text

from tradingagents.application.database import create_sqlite_engine
from tradingagents.persistence import upgrade_database


def test_upgrade_persists_revision_and_is_idempotent(app_settings):
    upgrade_database(app_settings)
    upgrade_database(app_settings)

    engine = create_sqlite_engine(
        app_settings.database_path,
        busy_timeout_ms=app_settings.busy_timeout_ms,
    )
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        inspector = inspect(engine)
        artifact_columns = {
            column["name"] for column in inspector.get_columns("run_artifacts")
        }
        run_columns = {
            column["name"] for column in inspector.get_columns("runs")
        }
        run_indexes = {
            index["name"] for index in inspector.get_indexes("runs")
        }
        artifact_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("run_artifacts")
        }
    finally:
        engine.dispose()

    assert revision == "0001_application_core"
    assert {
        "id",
        "run_id",
        "attempt",
        "stage",
        "role",
        "round",
        "schema_version",
        "generation_method",
        "content_type",
        "content_json",
        "content_hash",
        "created_at",
    } == artifact_columns
    assert (
        "run_id",
        "stage",
        "role",
        "round",
        "content_hash",
    ) in artifact_uniques
    assert "archived_at" in run_columns
    assert "ix_runs_archive" in run_indexes
