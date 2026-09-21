"""The offline cutover consumes a frozen predecessor, never mutable ORM schema."""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from tradingagents.persistence.cutover import MigrationError, migrate_current


@pytest.fixture
def source_0013(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as db:
        db.executescript((Path(__file__).parents[1] / "fixtures/schema_0013.sql").read_text())
        for identity, kind, status in [
            ("legacy", None, "succeeded"), ("baseline", "full", "succeeded"),
            ("failed-current", "full", "failed"), ("cancelled-current", "full", "cancelled"),
        ]:
            db.execute(
                "INSERT INTO runs (id,status,request_json,config_json,version,current_attempt,"
                "cancel_requested,metrics_json,created_at,updated_at,research_schema_version,"
                "research_kind,method_snapshot_json,submission_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, status, json.dumps({"ticker": "GOOG", "analysis_date": "2026-09-10"}),
                 '{"historical_missing_connection":true}', "0.4.0", 1, False, '{}',
                 "2026-09-10", "2026-09-10", "2" if kind else None, kind,
                 '{"recorded":true}' if kind else None, None),
            )
            db.execute(
                "INSERT INTO run_attempts (run_id,attempt,status,checkpoint_thread_id,resume_count,metrics_json) "
                "VALUES (?,1,?,?,0,'{}')", (identity, status, f"run:{identity}:attempt:1"),
            )
        db.execute("INSERT INTO research_nodes (run_id,research_kind,created_at) VALUES ('baseline','full','2026-09-10')")
        db.execute("INSERT INTO primary_research_cycles VALUES ('GOOG','baseline','2026-09-10','2026-09-10')")
    return source


def test_cutover_preserves_current_failed_runs_and_source_bytes(source_0013, tmp_path):
    before = sha256(source_0013.read_bytes()).hexdigest()
    destination = tmp_path / "current.db"
    report = migrate_current(source_0013, destination)
    assert sha256(source_0013.read_bytes()).hexdigest() == before
    assert report.removed_runs == 1
    assert report.retained_runs == 3
    assert report.retained_nodes == 1
    assert report.missing_fields == {"connection_identity": 3}
    with sqlite3.connect(destination) as db:
        assert {row[0] for row in db.execute('SELECT id FROM runs')} == {
            'baseline', 'failed-current', 'cancelled-current'
        }
        assert db.execute('SELECT count(*) FROM run_attempts').fetchone()[0] == 3
        assert db.execute('SELECT full_run_id FROM primary_research_cycles').fetchone()[0] == 'baseline'
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert json.loads(db.execute("SELECT audit_snapshot_json FROM runs WHERE id='baseline'").fetchone()[0])["config"] == {"historical_missing_connection": True}
        assert json.loads(db.execute("SELECT config_json FROM runs WHERE id='baseline'").fetchone()[0])["deep_binding"] is None


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_cutover_rejects_active_work(source_0013, tmp_path, status):
    with sqlite3.connect(source_0013) as db:
        db.execute("UPDATE runs SET status=? WHERE id='failed-current'", (status,))
    destination = tmp_path / 'new.db'
    with pytest.raises(MigrationError, match='queued or running'):
        migrate_current(source_0013, destination)
    assert not destination.exists()


def test_cutover_never_overwrites_destination(source_0013, tmp_path):
    destination = tmp_path / 'existing.db'
    destination.write_bytes(b'keep')
    with pytest.raises(MigrationError, match='exists'):
        migrate_current(source_0013, destination)
    assert destination.read_bytes() == b'keep'


def test_cutover_accepts_only_0013(source_0013, tmp_path):
    with sqlite3.connect(source_0013) as db:
        db.execute("UPDATE alembic_version SET version_num='0012_model_connections'")
    destination = tmp_path / 'new.db'
    with pytest.raises(MigrationError, match='0013'):
        migrate_current(source_0013, destination)
    assert not destination.exists()


def test_cutover_failure_never_publishes_partial_database(source_0013, tmp_path):
    with sqlite3.connect(source_0013) as db:
        db.execute("UPDATE primary_research_cycles SET full_run_id='missing'")
    destination = tmp_path / 'new.db'
    with pytest.raises(MigrationError, match='integrity'):
        migrate_current(source_0013, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob('.cutover-*'))


def test_normal_startup_rejects_predecessor_without_modifying_it(source_0013, tmp_path):
    from tradingagents.configuration.settings import AppSettings
    from tradingagents.persistence.migrations import IncompatibleDatabaseError, upgrade_database

    before = source_0013.read_bytes()
    settings = AppSettings.from_env(environ={
        'TRADINGAGENTS_HOME': str(tmp_path),
        'TRADINGAGENTS_DATABASE_PATH': str(source_0013),
    })
    with pytest.raises(IncompatibleDatabaseError, match='migrate-current'):
        upgrade_database(settings)
    assert source_0013.read_bytes() == before


def test_conversion_retains_connection_identity_secrets_and_reset_template(source_0013, tmp_path):
    from tradingagents.llm.models import preset_connection

    connection = preset_connection('deepseek', identity='retained')
    definition = connection.model_dump_json()
    with sqlite3.connect(source_0013) as db:
        db.execute("INSERT INTO model_connections VALUES (?,?,?)", ('retained', definition, 'deepseek'))
        db.execute("INSERT INTO configuration_credentials VALUES (?,?)", ('connection:retained:api_key', 'fixture-secret'))
        db.execute("INSERT INTO application_configuration VALUES (1,?,1,8,'2026-09-10')", ('{"deep_connection_id":"retained"}',))
    destination = tmp_path / 'new.db'
    report = migrate_current(source_0013, destination)
    assert 'fixture-secret' not in repr(report)
    with sqlite3.connect(destination) as db:
        assert json.loads(db.execute('SELECT definition FROM model_connections').fetchone()[0]) == json.loads(definition)
        assert db.execute('SELECT value FROM configuration_credentials').fetchone()[0] == 'fixture-secret'
        assert db.execute('SELECT revision FROM application_configuration').fetchone()[0] == 8


def test_cli_conversion_does_not_start_the_application(source_0013, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from tradingagents.cli import main

    monkeypatch.setattr(main, '_settings', lambda: pytest.fail('offline command must use explicit paths'))
    destination = tmp_path / 'cli.db'
    result = CliRunner().invoke(main.app, ['db', 'migrate-current', '--source', str(source_0013), '--destination', str(destination)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['retained_runs'] == 3


def test_converted_full_remains_a_baseline_for_offline_incremental(source_0013, tmp_path):
    from datetime import UTC, date, datetime

    from tests.application.test_incremental_v1_service import _incremental_service, _pit_collection
    from tests.factories import research_decision
    from tradingagents.configuration.settings import AppSettings
    from tradingagents.domain.collection import IncrementalEvidenceCandidate
    from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
    from tradingagents.domain.runs import AnalysisRequest
    from tradingagents.llm.models import preset_connection
    from tradingagents.persistence.repository import RunRepository

    baseline_item = EvidenceItem.create(source="fixture", evidence_type="filing",
        requested_date=date(2026, 9, 10), effective_date=date(2026, 9, 10),
        content="The retained baseline fact.")
    bundle = EvidenceBundle(instrument="GOOG", analysis_date=date(2026, 9, 10), items=(baseline_item,))
    decision = research_decision(evidence_refs=(baseline_item.ref,))
    connection = preset_connection("openai", identity="retained-connection")
    with sqlite3.connect(source_0013) as db:
        db.execute("UPDATE runs SET information_cutoff_at='2026-09-11T03:59:59.999999' WHERE id='baseline'")
        db.execute("INSERT INTO run_evidence VALUES ('baseline',1,?,?,1,0,'2026-09-11')", (bundle.model_dump_json(), bundle.digest))
        db.execute("INSERT INTO decisions (run_id,ticker,market,asset_type,analysis_date,rating,confidence,decision_json,created_at) VALUES ('baseline','GOOG','united_states','stock','2026-09-10',?,?,?,'2026-09-11')", (decision.rating.value, decision.confidence.value, decision.model_dump_json()))
        db.execute("INSERT INTO model_connections VALUES (?,?, 'openai')", (connection.id, connection.model_dump_json()))
        db.execute("INSERT INTO configuration_credentials VALUES (?, 'offline-fixture-key')", (f"connection:{connection.id}:api_key",))
        db.execute("INSERT INTO application_configuration VALUES (1,?,1,1,'2026-09-11')", (json.dumps({"llm_provider": "openai", "analysts": ["news"]}),))
    destination = tmp_path / "current-baseline.db"
    migrate_current(source_0013, destination)
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path), "TRADINGAGENTS_DATABASE_PATH": str(destination)}, load_env_files=False)
    repository = RunRepository(settings)
    candidate_item = EvidenceItem.create(source="fixture", evidence_type="filing",
        requested_date=date(2026, 9, 11), effective_date=date(2026, 9, 11),
        content="A newly published offline observation.")
    candidate = IncrementalEvidenceCandidate(evidence=candidate_item,
        available_on=date(2026, 9, 11))
    service = _incremental_service(settings, repository,
        collector=lambda request, *, data_context: _pit_collection(request, candidate),
        now=lambda: datetime(2026, 9, 12, 20, tzinfo=UTC))
    result = service.run(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-11",
        research_kind="incremental", full_baseline_run_id="baseline"))
    assert result.status.value == "succeeded"
    assert result.decision == decision
    assert repository.get_run(result.run_id).full_baseline_run_id == "baseline"
    assert repository.get_evidence("baseline").digest == bundle.digest
    assert repository.get_result("baseline").decision == decision


def test_conversion_normalizes_connection_effort_and_retains_audit(source_0013, tmp_path):
    from tradingagents.llm.models import ModelConnection, preset_connection

    old = preset_connection('openai', identity='retained').model_dump(mode='json')
    old.pop('reasoning_effort', None)
    old['reasoning_defaults'] = {'openai_reasoning_effort': 'high'}
    old['template'].pop('reasoning_effort', None)
    old['template']['reasoning_defaults'] = {'openai_reasoning_effort': 'medium'}
    snapshot = {'deep_binding': {'connection': old, 'model': 'gpt-5.5', 'reasoning_effort': None}}
    with sqlite3.connect(source_0013) as db:
        db.execute('INSERT INTO model_connections VALUES (?,?,?)', ('retained', json.dumps(old), 'openai'))
        db.execute("UPDATE runs SET config_json=? WHERE id='baseline'", (json.dumps(snapshot),))
    destination = tmp_path / 'new.db'
    migrate_current(source_0013, destination)
    with sqlite3.connect(destination) as db:
        converted = ModelConnection.model_validate_json(db.execute('SELECT definition FROM model_connections').fetchone()[0])
        assert converted.reasoning_effort == 'high'
        assert converted.template['reasoning_effort'] == 'medium'
        config, audit = db.execute("SELECT config_json, audit_snapshot_json FROM runs WHERE id='baseline'").fetchone()
        assert json.loads(config)['deep_binding']['connection']['reasoning_effort'] == 'high'
        assert json.loads(audit)['config'] == snapshot
