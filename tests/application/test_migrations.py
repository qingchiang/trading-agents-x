from __future__ import annotations

import sqlite3
from datetime import date, datetime
from importlib import resources

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    EvidenceBundle,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import create_sqlite_engine
from tradingagents.application.repository import RunRepository
from tradingagents.persistence import (
    IncompatibleDatabaseError,
    upgrade_database,
)


def _alembic_config(app_settings) -> Config:
    migration_root = resources.files("tradingagents.persistence").joinpath("alembic")
    with resources.as_file(migration_root) as script_location:
        config = Config()
        config.set_main_option("script_location", str(script_location))
        config.set_main_option(
            "sqlalchemy.url",
            f"sqlite+pysqlite:///{app_settings.database_path}",
        )
        config.attributes["busy_timeout_ms"] = app_settings.busy_timeout_ms
        return config


def test_upgrade_persists_revision_and_is_idempotent(app_settings):
    upgrade_database(app_settings)
    upgrade_database(app_settings)

    engine = create_sqlite_engine(
        app_settings.database_path,
        busy_timeout_ms=app_settings.busy_timeout_ms,
    )
    try:
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        inspector = inspect(engine)
        artifact_columns = {column["name"] for column in inspector.get_columns("run_artifacts")}
        outcome_columns = {column["name"] for column in inspector.get_columns("outcomes")}
        outcome_indexes = {index["name"] for index in inspector.get_indexes("outcomes")}
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        run_indexes = {index["name"] for index in inspector.get_indexes("runs")}
        revision_columns = {
            column["name"] for column in inspector.get_columns("research_revisions")
        }
        artifact_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("run_artifacts")
        }
        evidence_columns = {column["name"] for column in inspector.get_columns("run_evidence")}
        decision_columns = {column["name"] for column in inspector.get_columns("decisions")}
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert revision == "0007_shadow_research_updates"
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
        "generation_observations_json",
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
    assert "legacy_imports" not in table_names
    assert "trashed_at" in run_columns
    assert "instrument_local_name" in run_columns
    assert "research_chain_requested" in run_columns
    assert "update_intent_id" in run_columns
    assert "research_chain_id" in run_columns
    assert "baseline_revision_id" in run_columns
    assert "research_execution_strategy" in run_columns
    assert "research_update_audit_json" in run_columns
    assert "research_update_audit_json" in revision_columns
    assert "research_chains" in table_names
    assert "research_revisions" in table_names
    assert "ix_runs_trash" in run_indexes
    assert "next_check_at" in outcome_columns
    assert "ix_outcomes_due" in outcome_indexes
    assert "numeric_audit_json" in decision_columns


def test_v8_upgrade_preserves_research_data_and_downgrade_recreates_empty_table(
    app_settings,
) -> None:
    upgrade_database(app_settings, revision="0001_research_contract_v8")
    # The current ORM includes later fields. Temporarily add them only while
    # using the current repository to seed an otherwise-v1 database, then
    # remove them before exercising the real migration chain.
    seed_engine = create_sqlite_engine(app_settings.database_path)
    with seed_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN instrument_local_name VARCHAR(300)")
        connection.exec_driver_sql(
            "ALTER TABLE runs ADD COLUMN research_chain_requested BOOLEAN NOT NULL DEFAULT 0"
        )
        connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN update_intent_id VARCHAR(36)")
        connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN research_chain_id VARCHAR(36)")
        connection.exec_driver_sql("ALTER TABLE runs ADD COLUMN baseline_revision_id VARCHAR(36)")
        connection.exec_driver_sql(
            "ALTER TABLE runs ADD COLUMN research_execution_strategy VARCHAR(20)"
        )
        connection.exec_driver_sql(
            "ALTER TABLE runs ADD COLUMN research_update_audit_json JSON"
        )
    seed_engine.dispose()
    repository = RunRepository(app_settings)
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    run, _ = repository.create_run(request, {"fixture": True})
    repository.claim_run(run.id, "fixture-worker", 30)
    evidence = EvidenceBundle(
        instrument=request.ticker,
        analysis_date=request.analysis_date,
        items=(),
    )
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument=request.ticker,
            reports={},
            decision=research_decision(evidence_refs=()),
            evidence=evidence,
        ),
        evidence=evidence,
        benchmark="SPY",
    )
    outcome_id = repository.pending_outcomes(due_at=datetime(2100, 1, 1))[0]["outcome_id"]
    repository.resolve_outcome(
        outcome_id,
        observation_start=date(2026, 7, 25),
        observation_end=date(2026, 8, 1),
        raw_return=0.05,
        alpha_return=0.01,
        reflection="Preserved reflection.",
    )
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO legacy_imports "
            "(source_path, content_hash, status, run_id, imported_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ("/archive/memory.md", "a" * 64, "imported", run.id),
        )
    repository.engine.dispose()

    seed_engine = create_sqlite_engine(app_settings.database_path)
    with seed_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN instrument_local_name")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN research_chain_requested")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN update_intent_id")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN research_chain_id")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN baseline_revision_id")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN research_execution_strategy")
        connection.exec_driver_sql("ALTER TABLE runs DROP COLUMN research_update_audit_json")
    seed_engine.dispose()

    upgrade_database(app_settings)

    engine = create_sqlite_engine(app_settings.database_path)
    try:
        inspector = inspect(engine)
        assert "legacy_imports" not in inspector.get_table_names()
        with engine.connect() as connection:
            for table in (
                "runs",
                "run_evidence",
                "decisions",
                "outcomes",
                "reflections",
            ):
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
    finally:
        engine.dispose()

    command.downgrade(_alembic_config(app_settings), "0001_research_contract_v8")

    engine = create_sqlite_engine(app_settings.database_path)
    try:
        inspector = inspect(engine)
        assert "legacy_imports" in inspector.get_table_names()
        assert {
            "id",
            "source_path",
            "content_hash",
            "status",
            "run_id",
            "error_message",
            "imported_at",
        } == {column["name"] for column in inspector.get_columns("legacy_imports")}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM legacy_imports")) == 0
            assert connection.scalar(text("SELECT count(*) FROM runs")) == 1
    finally:
        engine.dispose()


def test_artifact_observation_migration_preserves_existing_rows(app_settings) -> None:
    upgrade_database(app_settings)
    repository = RunRepository(app_settings)
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    run, _ = repository.create_run(request, {"fixture": True})
    repository.claim_run(run.id, "fixture-worker", 30)
    original, _ = repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="analyst.market",
            stage="analyst",
            role="market",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=research_decision(evidence_refs=()),
        ),
    )
    repository.engine.dispose()

    config = _alembic_config(app_settings)
    command.downgrade(config, "0002_remove_legacy_imports")
    engine = create_sqlite_engine(app_settings.database_path)
    try:
        assert "generation_observations_json" not in {
            column["name"] for column in inspect(engine).get_columns("run_artifacts")
        }
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM run_artifacts")) == 1
    finally:
        engine.dispose()

    command.upgrade(config, "0003_artifact_generation_observations")
    repository = RunRepository(app_settings)
    try:
        restored = repository.list_artifacts(run.id)
        assert [item.id for item in restored] == [original.id]
        assert restored[0].generation_observations == ()
        observed, _ = repository.append_artifact(
            run.id,
            ResearchArtifactDraft(
                node="analyst.news",
                stage="analyst",
                role="news",
                generation_method=ArtifactGenerationMethod.JSON_MODE,
                generation_observations=(
                    ArtifactGenerationObservation(
                        node="analyst.news.serialize",
                        task_kind="semantic_structured",
                        client_role="quick_reasoning",
                        generation_method=ArtifactGenerationMethod.JSON_MODE,
                    ),
                ),
                content=research_decision(evidence_refs=()),
            ),
        )
        assert repository.list_artifacts(run.id)[1] == observed
    finally:
        repository.engine.dispose()

    command.downgrade(config, "0002_remove_legacy_imports")
    engine = create_sqlite_engine(app_settings.database_path)
    try:
        inspector = inspect(engine)
        assert "generation_observations_json" not in {
            column["name"] for column in inspector.get_columns("run_artifacts")
        }
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM run_artifacts")) == 2
    finally:
        engine.dispose()
    command.upgrade(config, "0003_artifact_generation_observations")


def test_unreleased_revision_requires_explicit_database_reset(
    app_settings,
) -> None:
    app_settings.prepare_filesystem()
    with sqlite3.connect(app_settings.database_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES ('0003_trash_lifecycle')")

    with pytest.raises(
        IncompatibleDatabaseError,
        match="remove .*tradingagents.db",
    ):
        upgrade_database(app_settings)
