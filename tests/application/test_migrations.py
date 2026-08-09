from __future__ import annotations

import json
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
    ResearchUpdateAudit,
    RunStatus,
)
from tradingagents.application.database import create_sqlite_engine
from tradingagents.application.repository import RunRepository
from tradingagents.application.research import EffectiveEvidenceSnapshot
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
        reflection_columns = {
            column["name"] for column in inspector.get_columns("reflections")
        }
        feedback_columns = {
            column["name"] for column in inspector.get_columns("outcome_feedback")
        }
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

    assert revision == "0009_outcome_feedback_lifecycle"
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
    assert {"role", "execution_strategy", "change_conclusion", "indeterminate_reason"}.issubset(
        revision_columns
    )
    assert "outcome" in revision_columns  # unreleased compatibility source, not public semantics
    assert "research_chains" in table_names
    assert "research_revisions" in table_names
    assert "ix_runs_trash" in run_indexes
    assert "next_check_at" in outcome_columns
    assert {
        "research_revision_id",
        "market_timezone",
        "method_category",
        "method_version",
        "price_semantics",
        "adjustment_semantics",
        "horizon_limit",
        "limitations_json",
        "data_available_at",
    }.issubset(outcome_columns)
    assert "ix_outcomes_due" in outcome_indexes
    assert {
        "status",
        "candidate_json",
        "generated_at",
        "last_attempted_at",
        "next_retry_at",
        "error_code",
    }.issubset(reflection_columns)
    assert {
        "reflection_id",
        "status",
        "reasons_json",
        "method_category",
        "horizon_limit",
        "applicability_json",
        "qualified_at",
        "available_at",
        "retired_at",
    }.issubset(feedback_columns)
    assert "numeric_audit_json" in decision_columns


def test_revision_semantic_migration_does_not_invent_initial_change_conclusion(
    app_settings,
) -> None:
    app_settings.prepare_filesystem()
    config = _alembic_config(app_settings)
    command.upgrade(config, "0007_shadow_research_updates")
    engine = create_sqlite_engine(app_settings.database_path)
    empty = '{"schema_version":"1"}'
    old_candidate_snapshot = {
        "schema_version": "1",
        "bundle": {
            "version": "8",
            "instrument": "6501.T",
            "analysis_date": "2026-07-25",
            "items": [
                {
                    "ref": "ev_aaaaaaaaaaaa",
                    "source": "TDnet",
                    "evidence_type": "bounded disclosure",
                    "requested_date": "2026-07-25",
                    "content": "Bounded candidate Evidence.",
                }
            ],
            "tables": [],
            "sealed_at": "2026-07-25T00:00:00Z",
            "digest": None,
        },
        "lineage": [{"evidence_ref": "ev_aaaaaaaaaaaa", "lineage": "new"}],
        "source_records": [],
        "source_record_lineage": [],
        "source_watermarks": [],
    }
    old_candidate_snapshot["bundle"] = EvidenceBundle.model_validate(
        old_candidate_snapshot["bundle"]
    ).model_dump(mode="json")
    authoritative_snapshot = {
        **old_candidate_snapshot,
        "bundle": EvidenceBundle.model_validate(
            {**old_candidate_snapshot["bundle"], "items": [], "digest": None}
        ).model_dump(mode="json"),
        "lineage": [],
    }
    old_audit = json.dumps(
        {
            "mode": "shadow",
            "candidate": {
                "outcome": "no_material_change",
                "coverage": {
                    "schema_version": "1",
                    "claims": [],
                    "questions": [],
                    "domains": [],
                    "limitations": [],
                    "supports_no_material_change": True,
                },
                "update_summary": {
                    "schema_version": "1",
                    "language": "en",
                    "summary": "No material change.",
                    "checked_domains": ["market"],
                    "outcome": "no_material_change",
                },
                "evidence_snapshot": old_candidate_snapshot,
            },
            "comparison": "agreement",
        }
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research_chains "
                "(id, instrument, is_primary, current_revision_id, created_at, updated_at) "
                "VALUES ('chain-1', '6501.T', 1, NULL, :now, :now)"
            ),
            {"now": datetime(2026, 7, 24)},
        )
        for revision_id, sequence, predecessor, outcome in (
            ("revision-1", 1, None, "material_change"),
            ("revision-2", 2, "revision-1", "no_material_change"),
            ("revision-3", 3, "revision-2", "coverage_incomplete"),
        ):
            connection.execute(
                text(
                    "INSERT INTO research_revisions "
                    "(id, chain_id, sequence, predecessor_revision_id, producing_run_id, "
                    "cutoff, execution_strategy, outcome, language, current_state_json, "
                    "delta_json, coverage_json, update_summary_json, evidence_snapshot_json, "
                    "research_update_audit_json, metrics_json, created_at) VALUES "
                    "(:id, 'chain-1', :sequence, :predecessor, NULL, :cutoff, 'full', "
                    ":outcome, 'en', :empty, :empty, :empty, :empty, :snapshot, :audit, :empty, :now)"
                ),
                {
                    "id": revision_id,
                    "sequence": sequence,
                    "predecessor": predecessor,
                    "cutoff": date(2026, 7, 23 + sequence),
                    "outcome": outcome,
                    "audit": old_audit if sequence == 2 else None,
                    "snapshot": (
                        json.dumps(authoritative_snapshot)
                        if sequence == 2
                        else empty
                    ),
                    "empty": empty,
                    "now": datetime(2026, 7, 24),
                },
            )
        connection.execute(
            text(
                "UPDATE research_chains SET current_revision_id = 'revision-3' "
                "WHERE id = 'chain-1'"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_sqlite_engine(app_settings.database_path)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT role, change_conclusion, indeterminate_reason "
                    "FROM research_revisions ORDER BY sequence"
                )
            ).all()
            upgraded_audit_value = connection.execute(
                text(
                    "SELECT research_update_audit_json FROM research_revisions "
                    "WHERE id = 'revision-2'"
                )
            ).scalar_one()
            upgraded_snapshot_value = connection.execute(
                text(
                    "SELECT evidence_snapshot_json FROM research_revisions "
                    "WHERE id = 'revision-2'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert rows == [
        ("initial", None, None),
        ("update", "no_material_change", None),
        ("update", "indeterminate", "coverage_incomplete"),
    ]
    upgraded_audit = ResearchUpdateAudit.model_validate(json.loads(upgraded_audit_value))
    assert upgraded_audit.schema_version == "2"
    assert upgraded_audit.candidate is not None
    assert upgraded_audit.candidate.change_conclusion == "no_material_change"
    assert upgraded_audit.candidate.evidence_snapshot.bundle.instrument == "6501.T"
    upgraded_snapshot = json.loads(upgraded_snapshot_value)
    assert upgraded_snapshot["bundle"]["items"][0]["ref"] == "ev_aaaaaaaaaaaa"
    assert upgraded_snapshot["bundle"]["digest"] is None
    validated_snapshot = EffectiveEvidenceSnapshot.model_validate(upgraded_snapshot)
    assert validated_snapshot.bundle.digest is not None

    upgraded_audit_json = upgraded_audit.model_dump(mode="json")
    upgraded_audit_json["comparison"] = "inconclusive"
    engine = create_sqlite_engine(app_settings.database_path)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER research_revisions_immutable_content")
        connection.execute(
            text(
                "UPDATE research_revisions SET research_update_audit_json = :audit "
                "WHERE id = 'revision-2'"
            ),
            {"audit": json.dumps(upgraded_audit_json)},
        )
    engine.dispose()

    command.downgrade(config, "0007_shadow_research_updates")
    engine = create_sqlite_engine(app_settings.database_path)
    try:
        with engine.connect() as connection:
            downgraded_rows = connection.execute(
                text("SELECT outcome FROM research_revisions ORDER BY sequence")
            ).scalars().all()
            downgraded_audit_value = connection.execute(
                text(
                    "SELECT research_update_audit_json FROM research_revisions "
                    "WHERE id = 'revision-2'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    downgraded_audit = json.loads(downgraded_audit_value)
    assert downgraded_rows == [
        "material_change",
        "no_material_change",
        "coverage_incomplete",
    ]
    assert "schema_version" not in downgraded_audit
    assert downgraded_audit["comparison"] == "not_applicable"
    assert downgraded_audit["candidate"]["outcome"] == "no_material_change"
    assert (
        downgraded_audit["candidate"]["evidence_snapshot"]["bundle"]["instrument"]
        == "6501.T"
    )


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
        for definition in (
            "research_revision_id VARCHAR(36)",
            "market_timezone VARCHAR(80)",
            "method_category VARCHAR(80)",
            "method_version VARCHAR(80)",
            "price_semantics VARCHAR(80)",
            "adjustment_semantics VARCHAR(80)",
            "horizon_limit TEXT",
            "limitations_json JSON",
            "data_available_at DATETIME",
        ):
            connection.exec_driver_sql(f"ALTER TABLE outcomes ADD COLUMN {definition}")
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
    with repository.engine.begin() as connection:
        outcome_id = connection.exec_driver_sql("SELECT id FROM outcomes").scalar_one()
        connection.exec_driver_sql(
            "UPDATE outcomes SET status = 'resolved', observation_start = ?, "
            "observation_end = ?, raw_return = 0.05, alpha_return = 0.01, "
            "resolved_at = CURRENT_TIMESTAMP, next_check_at = NULL WHERE id = ?",
            (date(2026, 7, 25), date(2026, 8, 1), outcome_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO reflections (outcome_id, text, created_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)",
            (outcome_id, "Preserved reflection."),
        )
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
        for column in (
            "research_revision_id",
            "market_timezone",
            "method_category",
            "method_version",
            "price_semantics",
            "adjustment_semantics",
            "horizon_limit",
            "limitations_json",
            "data_available_at",
        ):
            connection.exec_driver_sql(f"ALTER TABLE outcomes DROP COLUMN {column}")
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
                "outcome_feedback",
            ):
                assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 1
            observation = connection.execute(
                text(
                    "SELECT market_timezone, method_version, data_available_at "
                    "FROM outcomes"
                )
            ).one()
            assert observation.market_timezone == "America/New_York"
            assert observation.method_version == "short_term_relative_return.v1"
            assert observation.data_available_at is not None
            assert connection.scalar(text("SELECT status FROM reflections")) == "generated"
            assert connection.scalar(text("SELECT status FROM outcome_feedback")) == (
                "ineligible"
            )
            assert json.loads(
                connection.scalar(text("SELECT reasons_json FROM outcome_feedback"))
            ) == ["legacy_unqualified_reflection"]
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
