"""User-visible regression cases for configuration upgrade and replay."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from tradingagents.application.service import AnalysisService
from tradingagents.configuration.models import ConfigurationPatch
from tradingagents.configuration.settings import AppSettings
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.persistence import upgrade_database
from tradingagents.persistence._repository_common import IdempotencyConflictError
from tradingagents.persistence.configuration import ConfigurationStore


def configured(tmp_path):
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path)})
    upgrade_database(settings)
    store = ConfigurationStore(settings)
    store.save(
        ConfigurationPatch(revision=0, connection_changes=[{"action": "create", "id": "default", "preset": "openai", "credentials": {"api_key": "fake-key"}}]), initialize=True
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


def test_missing_original_submission_identity_is_an_explicit_conflict(tmp_path):
    settings, store, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    first = service.enqueue(request, idempotency_key="legacy")
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE runs SET submission_json=NULL WHERE id=?", (first.id,))
    store.save(ConfigurationPatch(revision=1, values={"profile": "deep"}))
    with pytest.raises(IdempotencyConflictError, match="not recorded"):
        service.enqueue(request, idempotency_key="legacy")
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(request.model_copy(update={"profile": "deep"}), idempotency_key="legacy")




@pytest.mark.parametrize("action", ["update", "delete"])
def test_incremental_ignores_retired_unused_quick_connection(tmp_path, action):
    from tests.application.test_model_connections import configured_store
    from tradingagents.configuration.errors import ConfigurationError

    _, store = configured_store(tmp_path)
    store.save(
        ConfigurationPatch(
            revision=1,
            values={'models': {'quick': {'connection_id': "secondary"}}},
            connection_changes=[
                {
                    "action": action,
                    "id": "primary",
                    **({"enabled": False} if action == "update" else {}),
                }
            ],
        )
    )
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10", research_kind="incremental", full_baseline_run_id="baseline", models={"deep": {"connection_id": "secondary"}})
    materialized, settings = store.resolve_request(request)
    assert settings.quick_binding is None
    assert materialized.models.quick is None
    assert store.execution_credentials(settings)
    with pytest.raises(ConfigurationError):
        store.resolve_request(
            AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10", models={'quick': {'connection_id': "primary"}, 'deep': {'connection_id': "secondary"}})
        )




def test_submission_null_inheritance_and_source_identity(tmp_path):
    _, store, service = configured(tmp_path)
    request = AnalysisRequest(ticker="GOOG", analysis_date="2026-09-10")
    original = service.enqueue(request, idempotency_key="inherit")
    store.save(
        ConfigurationPatch(
            revision=1,
            values={'models': {'quick': {'model': "changed-model"}, 'deep': {'model': "changed-deep-model"}}},
        )
    )
    assert (
        service.enqueue(
            request.model_copy(update={"connection_id": None, 'models': {'deep': {'model': None}}}),
            idempotency_key="inherit",
        ).id
        == original.id
    )
    with pytest.raises(IdempotencyConflictError):
        service.enqueue(request, idempotency_key="inherit", source_run_id=original.id)


def test_schema_groups_and_localized_choices_are_discoverable():
    from tradingagents.configuration.catalog import configuration_schema

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
