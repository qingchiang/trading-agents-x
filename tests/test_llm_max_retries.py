"""Run-scoped SDK retry configuration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tests.research_helpers import model_settings
from tradingagents.configuration.defaults import build_default_config
from tradingagents.configuration.settings import RunSettings
from tradingagents.llm.runtime import create_run_llms


def _settings(*, retries=None, provider="openai") -> RunSettings:
    return model_settings(
        provider=provider,
        llm_max_retries=retries,
        data_config=build_default_config(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("value", [0, 2, 10, "6"])
def test_run_settings_accept_non_negative_retry_budgets(value):
    assert _settings(retries=value).llm_max_retries == int(value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [-1, "-3", "abc", "1.5", True, False])
def test_run_settings_reject_invalid_retry_budgets(value):
    with pytest.raises(ValidationError):
        _settings(retries=value)


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["openai", "anthropic", "google"])
def test_retry_budget_is_forwarded_to_both_run_roles(monkeypatch, provider):
    calls = []

    def factory(provider, model, base_url=None, **kwargs):
        kwargs.update(provider=provider, model=model, base_url=base_url)
        calls.append(kwargs)
        client = MagicMock()
        client.get_llm.return_value = object()
        return client

    monkeypatch.setattr(
        "tradingagents.llm.connections.create_llm_client",
        factory,
    )

    create_run_llms(_settings(retries=6, provider=provider))

    assert len(calls) == 2
    assert [call["max_retries"] for call in calls] == [6, 6]


@pytest.mark.unit
def test_unset_retry_budget_preserves_provider_default(monkeypatch):
    calls = []

    def factory(provider, model, base_url=None, **kwargs):
        kwargs.update(provider=provider, model=model, base_url=base_url)
        calls.append(kwargs)
        client = MagicMock()
        client.get_llm.return_value = object()
        return client

    monkeypatch.setattr(
        "tradingagents.llm.connections.create_llm_client",
        factory,
    )

    create_run_llms(_settings())

    assert all("max_retries" not in call for call in calls)


@pytest.mark.unit
def test_environment_retry_string_is_resolved_by_explicit_import(tmp_path):
    from tests.configuration_helpers import import_configuration
    from tradingagents.configuration.settings import AppSettings
    settings = AppSettings.from_env(environ={"TRADINGAGENTS_HOME": str(tmp_path), "TRADINGAGENTS_LLM_MAX_RETRIES": "8"})
    assert import_configuration(settings).read().values.llm_max_retries == 8
