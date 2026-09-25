"""Quick and deep model settings remain isolated within one run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.research_helpers import model_settings
from tradingagents.configuration.defaults import build_default_config
from tradingagents.llm.reasoning_effort import RESOLVED_MARKER
from tradingagents.llm.runtime import create_run_llms


@pytest.mark.parametrize(
    ("provider", "quick_model", "deep_model", "native_key"),
    [
        (
            "openai",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "reasoning_effort",
        ),
        (
            "openai_compatible",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "reasoning_effort",
        ),
        (
            "azure",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "reasoning_effort",
        ),
        (
            "google",
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "thinking_level",
        ),
        (
            "anthropic",
            "claude-sonnet-5",
            "claude-fable-5",
            "effort",
        ),
    ],
)
def test_run_builds_independent_role_kwargs(
    monkeypatch,
    provider,
    quick_model,
    deep_model,
    native_key,
):
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
    settings = model_settings(
        provider=provider,
        quick=quick_model,
        deep=deep_model,
        quick_effort="low",
        deep_effort="high",
        temperature=0.2,
        llm_max_retries=4,
        data_config=build_default_config(),
    )

    create_run_llms(settings, callbacks=[object()])

    assert {call["model"]: call[native_key] for call in calls} == {quick_model: "low", deep_model: "high"}
    assert all(call["temperature"] == 0.2 for call in calls)
    assert all(call["max_retries"] == 4 for call in calls)
    assert all(call[RESOLVED_MARKER] is True for call in calls)


def test_deepseek_role_efforts_are_not_cross_wired(monkeypatch):
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
    settings = model_settings(
        provider="deepseek",
        quick="deepseek-v4-flash",
        deep="deepseek-v4-flash",
        quick_effort="low",
        deep_effort="high",
        data_config=build_default_config(),
    )

    llms = create_run_llms(settings)

    reasoning = [call for call in calls if "reasoning_effort" in call]
    serializers = [call for call in calls if "reasoning_effort" not in call]
    assert sorted(call["reasoning_effort"] for call in reasoning) == ["high", "low"]
    assert len(serializers) == 2
    assert all(call["model"] == "deepseek-v4-flash" for call in calls)
    assert all(call["extra_body"] == {"thinking": {"type": "disabled"}} for call in serializers)
    assert llms.quick is not llms.quick_serializer
    assert llms.deep is not llms.deep_serializer
