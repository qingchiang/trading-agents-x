from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from importlib import resources

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import RunRecord, create_sqlite_engine
from tradingagents.application.repository import RunRepository
from tradingagents.application.service import AnalysisService
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
        run_columns = {column["name"] for column in inspector.get_columns("runs")}
        run_indexes = {index["name"] for index in inspector.get_indexes("runs")}
        artifact_uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("run_artifacts")
        }
        evidence_columns = {column["name"] for column in inspector.get_columns("run_evidence")}
        decision_columns = {column["name"] for column in inspector.get_columns("decisions")}
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert revision == "0008_incremental_node_products"
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
    assert {
        "research_schema_version",
        "information_cutoff_at",
        "method_snapshot_json",
        "research_kind",
        "full_baseline_run_id",
        "incremental_cutoff",
        "incremental_input_fingerprint",
    } <= run_columns
    assert "ix_runs_trash" in run_indexes
    assert "outcomes" not in table_names
    assert "reflections" not in table_names
    assert "numeric_audit_json" in decision_columns
    node_columns = {column["name"] for column in inspector.get_columns("research_nodes")}
    assert "incremental_products_json" in node_columns


def test_branch3_upgrade_discards_legacy_reviews_and_preserves_execution_history(
    app_settings,
    tmp_path,
) -> None:
    # Create retained execution history with the current mapper, then move the
    # real database back to the released predecessor before adding retired
    # review rows. This keeps the fixture independent of newer ORM columns.
    upgrade_database(app_settings)
    repository = RunRepository(app_settings)
    fixtures: dict[str, tuple] = {}
    for ticker in ("NVDA", "AAPL"):
        request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")
        run, _ = repository.create_run(request, {"fixture": True})
        repository.claim_run(run.id, "fixture-worker", 30)
        repository.append_event(
            run.id,
            "run.started",
            node="fixture.worker",
            payload={"source": "migration-test"},
        )
        evidence_item = EvidenceItem.create(
            source="fixture",
            evidence_type="price",
            requested_date=request.analysis_date,
            effective_date=request.analysis_date,
            value=100.0,
            unit="USD",
        )
        evidence = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            items=(evidence_item,),
        )
        report = analyst_report(evidence_ref=evidence_item.ref)
        decision = research_decision(evidence_refs=(evidence_item.ref,))
        repository.append_artifact(
            run.id,
            ResearchArtifactDraft(
                node="analyst.market",
                stage="analyst",
                role="market",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=report,
            ),
        )
        repository.seal_evidence(run.id, evidence)
        repository.complete(
            run.id,
            AnalysisResult(
                run_id=run.id,
                status=RunStatus.SUCCEEDED,
                instrument=request.ticker,
                reports={"market": report},
                decision=decision,
                evidence=evidence,
            ),
            evidence=evidence,
        )
        fixtures[ticker] = (run.id, request, evidence, report, decision)

    # A retained Crypto snapshot is legacy data, not a new creation request.
    crypto_id = fixtures["AAPL"][0]
    with repository.sessions.begin() as session:
        crypto_record = session.get(RunRecord, crypto_id)
        crypto_record.request_json = {
            **crypto_record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
            "analysts": ["market", "social", "news"],
        }

    repository.engine.dispose()
    config = _alembic_config(app_settings)
    command.downgrade(config, "0004_instrument_local_name")
    repository = RunRepository(app_settings)

    # Seed both review states and their child reflections at the predecessor
    # head. The migration must intentionally discard all four rows.
    with repository.engine.begin() as connection:
        decision_ids = {
            run_id: connection.exec_driver_sql(
                "SELECT id FROM decisions WHERE run_id = ?", (run_id,)
            ).scalar_one()
            for run_id, *_ in fixtures.values()
        }
        connection.exec_driver_sql(
            "INSERT INTO outcomes "
            "(decision_id, status, benchmark, holding_intervals, next_check_at) "
            "VALUES (?, 'pending', 'SPY', 5, '2026-07-24 00:00:00')",
            (decision_ids[fixtures["NVDA"][0]],),
        )
        connection.exec_driver_sql(
            "INSERT INTO outcomes "
            "(decision_id, status, benchmark, observation_start, "
            "observation_end, holding_intervals, raw_return, alpha_return, "
            "resolved_at) VALUES (?, 'resolved', 'SPY', '2026-07-25', "
            "'2026-08-01', 5, 0.05, 0.01, '2026-08-01 00:00:00')",
            (decision_ids[fixtures["AAPL"][0]],),
        )
        outcome_ids = connection.exec_driver_sql("SELECT id FROM outcomes ORDER BY id").fetchall()
        for (outcome_id,) in outcome_ids:
            connection.exec_driver_sql(
                "INSERT INTO reflections (outcome_id, text, created_at) "
                "VALUES (?, ?, '2026-08-01 00:00:00')",
                (outcome_id, f"Legacy reflection {outcome_id}."),
            )
        assert len(outcome_ids) == 2
    repository.engine.dispose()

    with sqlite3.connect(app_settings.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM reflections").fetchone()[0] == 2

    upgrade_database(app_settings)

    engine = create_sqlite_engine(app_settings.database_path)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert "research_nodes" in table_names
        assert "primary_research_cycles" in table_names
        assert "research_timelines" not in table_names
        assert "outcomes" not in table_names
        assert "reflections" not in table_names
        with engine.connect() as connection:
            expected_counts = {
                "runs": 2,
                "run_attempts": 2,
                "run_events": 6,
                "run_artifacts": 2,
                "run_evidence": 2,
                "decisions": 2,
            }
            for table, expected_count in expected_counts.items():
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == expected_count
            assert connection.scalar(text("SELECT count(*) FROM research_nodes")) == 0
            assert connection.scalar(text("SELECT count(*) FROM primary_research_cycles")) == 0
    finally:
        engine.dispose()

    history_repository = RunRepository(app_settings)
    try:
        for ticker, (run_id, request, evidence, report, decision) in fixtures.items():
            result = history_repository.get_result(run_id)
            assert result.status is RunStatus.SUCCEEDED
            assert result.evidence == evidence
            assert result.reports == {"market": report}
            assert result.decision == decision
            assert history_repository.list_attempts(run_id)[0].status is RunStatus.SUCCEEDED
            assert history_repository.list_events(run_id)
            assert history_repository.list_artifacts(run_id)
            history_view = history_repository.get_run(run_id)
            expected_ticker = "BTC-USD" if ticker == "AAPL" else request.ticker
            expected_asset_type = "crypto" if ticker == "AAPL" else request.asset_type
            assert history_view.request.ticker == expected_ticker
            assert history_view.request.asset_type == expected_asset_type

        crypto_view = history_repository.get_run(fixtures["AAPL"][0])
        assert crypto_view.request.ticker == "BTC-USD"
        assert crypto_view.request.asset_type == "crypto"

        export_service = AnalysisService(app_settings, repository=history_repository)
        crypto_run_id = fixtures["AAPL"][0]
        json_type, export_json = export_service.export(crypto_run_id, format="json")
        markdown_type, export_markdown = export_service.export(crypto_run_id, format="markdown")
        package_type, export_package = export_service.export(crypto_run_id, format="package")
        assert json_type == "application/json"
        assert '"run_id"' in export_json
        assert markdown_type == "text/markdown; charset=utf-8"
        assert "Fixture evidence-grounded analysis." in export_markdown
        assert package_type == "application/zip"
        with zipfile.ZipFile(io.BytesIO(export_package)) as archive:
            assert json.loads(archive.read("run.json"))["run"]["request"]["asset_type"] == "crypto"
        trashed, changed = history_repository.trash_runs((crypto_run_id,))
        assert changed == 1
        assert trashed[0].trashed_at is not None
        assert trashed[0].request.asset_type == "crypto"
        restored_view, changed = history_repository.restore_runs((crypto_run_id,))
        assert changed == 1
        assert restored_view[0].trashed_at is None
        assert restored_view[0].request.asset_type == "crypto"
    finally:
        history_repository.engine.dispose()

    backup_repository = RunRepository(app_settings)
    try:
        backup_path = backup_repository.backup(tmp_path / "backup" / "post-review-removal.db")
    finally:
        backup_repository.engine.dispose()
    with sqlite3.connect(backup_path) as connection:
        backup_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert {
            "runs",
            "run_attempts",
            "run_events",
            "run_artifacts",
            "run_evidence",
            "decisions",
        } <= backup_tables
        assert "outcomes" not in backup_tables
        assert "reflections" not in backup_tables
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT json_extract(request_json, '$.asset_type') "
                "FROM runs WHERE json_extract(request_json, '$.ticker') = 'BTC-USD'"
            ).fetchone()[0]
            == "crypto"
        )


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
