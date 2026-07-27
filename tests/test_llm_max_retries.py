"""Run-scoped SDK retry configuration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from tradingagents.application.llms import create_run_llms
from tradingagents.application.settings import RunSettings
from tradingagents.default_config import build_default_config


def _settings(*, retries=None, provider="openai") -> RunSettings:
    return RunSettings(
        llm_provider=provider,
        llm_max_retries=retries,
        data_config=build_default_config({}),
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

    def factory(**kwargs):
        calls.append(kwargs)
        client = MagicMock()
        client.get_llm.return_value = object()
        return client

    monkeypatch.setattr(
        "tradingagents.application.llms.create_llm_client",
        factory,
    )

    create_run_llms(_settings(retries=6, provider=provider))

    assert len(calls) == 2
    assert [call["max_retries"] for call in calls] == [6, 6]


@pytest.mark.unit
def test_unset_retry_budget_preserves_provider_default(monkeypatch):
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        client = MagicMock()
        client.get_llm.return_value = object()
        return client

    monkeypatch.setattr(
        "tradingagents.application.llms.create_llm_client",
        factory,
    )

    create_run_llms(_settings())

    assert all("max_retries" not in call for call in calls)


@pytest.mark.unit
def test_environment_retry_string_is_resolved_at_settings_boundary():
    config = build_default_config(
        {"TRADINGAGENTS_LLM_MAX_RETRIES": "8"}
    )

    settings = RunSettings(
        llm_max_retries=config["llm_max_retries"],
        data_config=config,
    )

    assert settings.llm_max_retries == 8
