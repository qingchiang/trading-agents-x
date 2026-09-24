"""Upgrade real predecessor rows through the public migration/repository seams."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

import pytest

from tests.research.test_decision_references import reference_fixture
from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.domain.artifacts import ResearchArtifactDraft
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.runs import AnalysisRequest, AnalysisResult
from tradingagents.persistence import upgrade_database
from tradingagents.persistence.configuration import ConfigurationStore
from tradingagents.persistence.repository import RunRepository


def seed_predecessor(settings):
    upgrade_database(settings, revision="0100_independent")
    ConfigurationStore(settings).save(
        ConfigurationPatch(
            revision=0,
            connection_changes=[
                {
                    "action": "create",
                    "id": "default",
                    "preset": "openai",
                    "credentials": {"api_key": "placeholder"},
                }
            ],
        ),
        initialize=True,
    )
    repo = RunRepository(settings)
    request = AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 24))
    config = ConfigurationStore(settings).resolve_request(request, require_initialized=False)[1]
    run, _ = repo.create_run(request, config.snapshot())
    repo.claim_run(run.id, "fixture", 30)
    payload, bundle = reference_fixture()
    decision = ResearchDecision.model_validate(payload)
    repo.seal_evidence(run.id, bundle)
    repo.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="decision",
            stage="decision",
            role="final_committee",
            generation_method="tool_call",
            content=decision,
        ),
    )
    repo.complete(
        run.id,
        AnalysisResult(
            run_id=run.id, status="succeeded", instrument="NVDA", reports={}, decision=decision
        ),
        evidence=bundle,
    )
    legacy = decision.model_dump(mode="json")
    legacy.update(
        valuation_assessment=None,
        calculation_records=[{"id": "calc_old", "formula": "100 + 20"}],
        numeric_audit_status="partial",
    )
    legacy["scenarios"][0]["reference_ranges"][0]["low"]["calculation_id"] = "calc_old"
    legacy["scenarios"][0]["reference_ranges"][0]["high"]["calculation_id"] = "calc_high"
    legacy["market_reference_levels"][0]["calculation_ids"] = ["calc_old"]
    with sqlite3.connect(settings.database_path) as c:
        c.execute("UPDATE runs SET research_schema_version='2'")
        c.execute(
            "UPDATE decisions SET decision_json=?, numeric_audit_json=?",
            (json.dumps(legacy), json.dumps({"status": "partial"})),
        )
        c.execute(
            "UPDATE run_artifacts SET content_json=?,schema_version='2',content_hash='old-hash'",
            (json.dumps(legacy),),
        )
    repo.engine.dispose()
    return run.id, decision, bundle


def test_upgrade_preserves_references_history_and_full_baseline(app_settings):
    run_id, expected, evidence = seed_predecessor(app_settings)
    upgrade_database(app_settings)
    upgrade_database(app_settings)
    repo = RunRepository(app_settings)
    try:
        result = repo.get_result(run_id)
        assert result.decision == expected
        assert result.evidence == evidence
        assert repo.list_artifacts(run_id)[0].content == expected
        assert repo.list_artifacts(run_id)[0].schema_version == "3"
        repo.validate_incremental_baseline(
            run_id,
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 25),
                research_kind="incremental",
                full_baseline_run_id=run_id,
            ),
        )
        # Hash is part of the durable artifact contract, checked against its
        # public canonical payload rather than the migration implementation.
        with sqlite3.connect(app_settings.database_path) as c:
            content, digest = c.execute(
                "SELECT content_json,content_hash FROM run_artifacts"
            ).fetchone()
            canonical = json.dumps(
                expected.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assert json.loads(content) == expected.model_dump(mode="json")
            assert digest == hashlib.sha256(canonical.encode()).hexdigest()
            assert not c.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        repo.engine.dispose()


def test_conversion_failure_rolls_back_rows_and_revision(app_settings):
    seed_predecessor(app_settings)
    with sqlite3.connect(app_settings.database_path) as c:
        c.execute("UPDATE run_artifacts SET content_json='not json'")
        before = c.execute("SELECT decision_json FROM decisions").fetchone()[0]
    with pytest.raises(ValueError):
        upgrade_database(app_settings)
    with sqlite3.connect(app_settings.database_path) as c:
        assert c.execute("SELECT decision_json FROM decisions").fetchone()[0] == before
        assert (
            c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0100_independent"
        )
        assert "numeric_audit_json" in {r[1] for r in c.execute("PRAGMA table_info(decisions)")}


@pytest.mark.parametrize("table", ["checkpoints", "writes"])
def test_upgrade_rejects_retained_checkpoint_without_deleting_it(app_settings, table):
    upgrade_database(app_settings, revision="0100_independent")
    with sqlite3.connect(app_settings.database_path) as c:
        c.execute(f"CREATE TABLE {table} (id TEXT)")
        c.execute(f"INSERT INTO {table} VALUES ('retain-me')")
    with pytest.raises(RuntimeError, match="checkpoints"):
        upgrade_database(app_settings)
    with sqlite3.connect(app_settings.database_path) as c:
        assert c.execute(f"SELECT id FROM {table}").fetchone()[0] == "retain-me"


def test_revision_write_failure_rolls_back_column_removal(app_settings):
    seed_predecessor(app_settings)
    with sqlite3.connect(app_settings.database_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_revision BEFORE UPDATE ON alembic_version BEGIN SELECT RAISE(ABORT, 'fixture revision failure'); END"
        )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="fixture revision failure"):
        upgrade_database(app_settings)
    with sqlite3.connect(app_settings.database_path) as connection:
        assert "numeric_audit_json" in {
            row[1] for row in connection.execute("PRAGMA table_info(decisions)")
        }
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0100_independent"
        )
        assert (
            connection.execute("SELECT content_hash FROM run_artifacts").fetchone()[0] == "old-hash"
        )
