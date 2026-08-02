from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect, text

from tradingagents.application.database import create_sqlite_engine
from tradingagents.persistence import (
    IncompatibleDatabaseError,
    upgrade_database,
)


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
        evidence_columns = {
            column["name"] for column in inspector.get_columns("run_evidence")
        }
        decision_columns = {
            column["name"] for column in inspector.get_columns("decisions")
        }
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert revision == "0001_research_contract_v8"
    assert {
        "id",
        "run_id",
        "attempt",
        "stage",
        "role",
        "round",
        "schema_version",
        "prompt_version",
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
        "prompt_version",
    ) in artifact_uniques
    assert evidence_columns == {
        "run_id",
        "sealed_attempt",
        "bundle_json",
        "digest",
        "item_count",
        "table_count",
        "sealed_at",
    }
    assert "reports" not in table_names
    assert "trashed_at" in run_columns
    assert "ix_runs_trash" in run_indexes
    assert "next_check_at" in outcome_columns
    assert "ix_outcomes_due" in outcome_indexes
    assert "numeric_audit_json" in decision_columns


def test_unreleased_revision_requires_explicit_database_reset(
    app_settings,
) -> None:
    app_settings.prepare_filesystem()
    with sqlite3.connect(app_settings.database_path) as connection:
        connection.execute(
            "CREATE TABLE alembic_version "
            "(version_num VARCHAR(32) NOT NULL)"
        )
        connection.execute(
            "INSERT INTO alembic_version VALUES ('0003_trash_lifecycle')"
        )

    with pytest.raises(
        IncompatibleDatabaseError,
        match="remove .*tradingagents.db",
    ):
        upgrade_database(app_settings)
