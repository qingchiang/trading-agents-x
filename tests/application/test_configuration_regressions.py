"""User-visible regression cases for configuration upgrade and replay."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from tradingagents.application.configuration import ConfigurationStore
from tradingagents.application.configuration_models import ConfigurationPatch
from tradingagents.application.contracts import AnalysisRequest
from tradingagents.application.model_connections import legacy_connection_id
from tradingagents.application.repository import IdempotencyConflictError
from tradingagents.application.service import AnalysisService
from tradingagents.application.settings import AppSettings
from tradingagents.persistence import upgrade_database


def configured(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(revision=0, credentials={"OPENAI_API_KEY": "fake-key"}), initialize=True
    )
    service = AnalysisService(
        settings,
        eligibility_resolver=lambda ticker: {"symbol": ticker, "quote_type": "EQUITY"},
        identity_resolver=lambda ticker, date: {"company_name": ticker},
    )
    return settings, store, service


def test_replay_keeps_original_run_after_defaults_and_connections_change(tmp_path, monkeypatch):
    _, store, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    first = service.enqueue(request, idempotency_key="retry-http")
    events = service.repository.list_events(first.id)
    store.save(ConfigurationPatch(revision=1, values={"profile": "deep", "output_language": "ja"}))
    monkeypatch.setattr(
        service.configuration,
        "resolve_request",
        Mock(side_effect=AssertionError("replay must not resolve defaults")),
    )
    monkeypatch.setattr(
        service.configuration,
        "execution_credentials",
        Mock(side_effect=AssertionError("replay must not read keys")),
    )
    replay = service.enqueue(request, idempotency_key="retry-http")
    assert replay.id == first.id
    assert service.repository.list_events(first.id) == events
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(
            request.model_copy(update={"profile": "standard"}), idempotency_key="retry-http"
        )


def test_concurrent_identical_submissions_share_one_run(tmp_path):
    _, _, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(
            pool.map(lambda _: service.enqueue(request, idempotency_key="concurrent"), range(2))
        )
    assert runs[0].id == runs[1].id
    assert len(service.repository.list_events(runs[0].id)) == 1


def test_legacy_replay_compares_retained_defaults(tmp_path):
    settings, store, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    first = service.enqueue(request, idempotency_key="legacy")
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE runs SET submission_json=NULL WHERE id=?", (first.id,))
    store.save(ConfigurationPatch(revision=1, values={"profile": "deep"}))
    assert service.enqueue(request, idempotency_key="legacy").id == first.id
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(request.model_copy(update={"profile": "deep"}), idempotency_key="legacy")


def test_upgrade_captures_current_legacy_connection_as_reset_baseline(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings, "0012_model_connections")
    identity = legacy_connection_id("openai")
    with sqlite3.connect(settings.database_path) as db:
        definition = json.loads(
            db.execute(
                "SELECT definition FROM model_connections WHERE id=?", (identity,)
            ).fetchone()[0]
        )
        definition["transport"] = {
            "kind": "chat_completions",
            "base_url": "https://saved.example/v1",
        }
        db.execute(
            "UPDATE model_connections SET definition=? WHERE id=?",
            (json.dumps(definition), identity),
        )
        db.execute("INSERT INTO application_configuration VALUES (1, '{}', 1, 7, '2026-09-10')")
        db.execute(
            "INSERT INTO configuration_credentials VALUES (?, ?)",
            (f"connection:{identity}:api_key", "preserved-key"),
        )
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    view = store.read()
    assert view.initialized and view.revision == 8
    conn = view.connections[identity].connection
    assert conn.template_origin == "upgrade"
    saved = store.save(
        ConfigurationPatch(
            revision=8,
            connection_changes=[
                {
                    "action": "update",
                    "id": identity,
                    "transport": {
                        "kind": "chat_completions",
                        "base_url": "https://edited.example/v1",
                    },
                }
            ],
        )
    )
    reset = store.save(
        ConfigurationPatch(
            revision=saved.revision, connection_changes=[{"action": "reset", "id": identity}]
        )
    )
    assert reset.connections[identity].connection.transport.base_url == "https://saved.example/v1"
    assert store.reveal_connection(identity, "api_key") == "preserved-key"


@pytest.mark.parametrize("action", ["update", "delete"])
def test_incremental_ignores_retired_unused_quick_connection(tmp_path, action):
    from tests.application.test_model_connections import configured_store
    from tradingagents.application.configuration import ConfigurationError

    _, store = configured_store(tmp_path)
    store.save(
        ConfigurationPatch(
            revision=1,
            values={"quick_connection_id": "secondary"},
            connection_changes=[
                {
                    "action": action,
                    "id": "primary",
                    **({"enabled": False} if action == "update" else {}),
                }
            ],
        )
    )
    request = AnalysisRequest(
        ticker="GOOG",
        analysis_date="2026-09-10",
        research_kind="incremental",
        full_baseline_run_id="baseline",
        quick_connection_id="primary",
        deep_connection_id="secondary",
        quick_reasoning_effort="unsupported-ignored",
        quick_model="ignored",
    )
    materialized, settings = store.resolve_request(request)
    assert settings.quick_binding == settings.deep_binding
    assert materialized.quick_connection_id == "secondary"
    assert store.execution_credentials(settings)
    with pytest.raises(ConfigurationError):
        store.resolve_request(
            AnalysisRequest(
                ticker="GOOG",
                analysis_date="2026-09-10",
                quick_connection_id="primary",
                deep_connection_id="secondary",
            )
        )


def test_upgrade_preserves_new_connection_baselines_and_history(tmp_path):
    from alembic import command

    from tests.application.test_migrations import _alembic_config
    from tests.application.test_model_connections import configured_store

    settings, store = configured_store(tmp_path)
    before = store.read()
    service = AnalysisService(
        settings,
        eligibility_resolver=lambda ticker: {"symbol": ticker, "quote_type": "EQUITY"},
        identity_resolver=lambda ticker, date: {"company_name": ticker},
    )
    run = service.enqueue(AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10"))
    command.downgrade(_alembic_config(settings), "0012_model_connections")
    with sqlite3.connect(settings.database_path) as db:
        columns = "request_json, config_json, method_snapshot_json"
        retained = db.execute(f"SELECT {columns} FROM runs WHERE id=?", (run.id,)).fetchone()
    upgrade_database(settings)
    after = store.read()
    assert after.revision == before.revision
    assert after.initialized == before.initialized
    assert after.connections == before.connections
    with sqlite3.connect(settings.database_path) as db:
        assert (
            db.execute(f"SELECT {columns} FROM runs WHERE id=?", (run.id,)).fetchone() == retained
        )
        assert db.execute("SELECT submission_json FROM runs WHERE id=?", (run.id,)).fetchone() == (
            None,
        )


def test_submission_null_inheritance_and_source_identity(tmp_path):
    _, store, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    original = service.enqueue(request, idempotency_key="inherit")
    store.save(
        ConfigurationPatch(
            revision=1,
            values={"quick_think_llm": "changed-model", "deep_think_llm": "changed-deep-model"},
        )
    )
    assert (
        service.enqueue(
            request.model_copy(update={"connection_id": None, "deep_model": None}),
            idempotency_key="inherit",
        ).id
        == original.id
    )
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(request, idempotency_key="inherit", source_run_id=original.id)


def test_schema_groups_and_localized_choices_are_discoverable():
    from tradingagents.application.configuration_catalog import configuration_schema

    schema = configuration_schema()
    for field in schema.fields:
        assert field.group in schema.group_descriptions
        assert field.description != schema.group_descriptions[field.group]
        if field.key in {"profile", "analysts", "openai_reasoning_effort"}:
            assert set(field.options) == set(field.option_labels)
            assert all(
                set(labels) == {"en", "zh-CN", "ja"} for labels in field.option_labels.values()
            )
    assert schema.credential_metadata["FRED_API_KEY"].label == "FRED"


def test_incremental_legacy_provider_ignores_quick_identity(tmp_path):
    _, store, _ = configured(tmp_path)
    request = AnalysisRequest(
        ticker="GOOG", analysis_date="2026-09-10", research_kind="incremental",
        full_baseline_run_id="baseline", llm_provider="openai", quick_connection_id="deleted",
    )
    materialized, settings = store.resolve_request(request)
    assert materialized.deep_connection_id == legacy_connection_id("openai")
    assert settings.quick_binding == settings.deep_binding
