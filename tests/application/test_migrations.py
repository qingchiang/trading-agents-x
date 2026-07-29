from __future__ import annotations

from datetime import datetime

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
        outcome_columns = {
            column["name"] for column in inspector.get_columns("outcomes")
        }
        outcome_indexes = {
            index["name"] for index in inspector.get_indexes("outcomes")
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

    assert revision == "0002_outcome_schedule"
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
    assert "next_check_at" in outcome_columns
    assert "ix_outcomes_due" in outcome_indexes


def test_outcome_schedule_migration_backfills_existing_pending_rows(
    app_settings,
) -> None:
    upgrade_database(app_settings, "0001_application_core")
    engine = create_sqlite_engine(
        app_settings.database_path,
        busy_timeout_ms=app_settings.busy_timeout_ms,
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO runs (
                        id, status, request_json, config_json, version,
                        current_attempt, cancel_requested, metrics_json,
                        created_at, updated_at
                    ) VALUES (
                        'run-1', 'succeeded', '{}', '{}', 'test',
                        1, 0, '{}', :now, :now
                    )
                    """
                ),
                {"now": datetime(2026, 7, 29)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO decisions (
                        run_id, ticker, market, asset_type, analysis_date,
                        rating, confidence, decision_json,
                        evidence_bundle_json, created_at
                    ) VALUES (
                        'run-1', '6501.T', 'JP', 'stock', '2026-07-28',
                        'Hold', 0.5, '{}', '{}', :now
                    )
                    """
                ),
                {"now": datetime(2026, 7, 29)},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO outcomes (
                        decision_id, status, benchmark, holding_intervals,
                        last_checked_at
                    ) VALUES (
                        1, 'pending', '^N225', 5, :last_checked
                    )
                    """
                ),
                {"last_checked": datetime(2026, 7, 29, 1)},
            )
    finally:
        engine.dispose()

    upgrade_database(app_settings)

    engine = create_sqlite_engine(
        app_settings.database_path,
        busy_timeout_ms=app_settings.busy_timeout_ms,
    )
    try:
        with engine.connect() as connection:
            due = connection.scalar(
                text("SELECT next_check_at FROM outcomes WHERE id = 1")
            )
    finally:
        engine.dispose()

    assert datetime.fromisoformat(str(due)) == datetime(2026, 8, 4, 15)
