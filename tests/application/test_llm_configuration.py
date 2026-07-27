"""Quick and deep model settings remain isolated within one run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.application.llms import create_run_llms
from tradingagents.application.settings import RunSettings
from tradingagents.default_config import build_default_config
from tradingagents.llm_clients.reasoning_effort import RESOLVED_MARKER


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

    def factory(**kwargs):
        calls.append(kwargs)
        client = MagicMock()
        client.get_llm.return_value = object()
        return client

    monkeypatch.setattr(
        "tradingagents.application.llms.create_llm_client",
        factory,
    )
    settings = RunSettings(
        llm_provider=provider,
        quick_model=quick_model,
        deep_model=deep_model,
        quick_reasoning_effort="low",
        deep_reasoning_effort="high",
        temperature=0.2,
        llm_max_retries=4,
        data_config=build_default_config({}),
    )

    create_run_llms(settings, callbacks=[object()])

    assert [call[native_key] for call in calls] == ["low", "high"]
    assert all(call["temperature"] == 0.2 for call in calls)
    assert all(call["max_retries"] == 4 for call in calls)
    assert all(call[RESOLVED_MARKER] is True for call in calls)
    assert calls[0]["model"] == quick_model
    assert calls[1]["model"] == deep_model


def test_deepseek_role_efforts_are_not_cross_wired(monkeypatch):
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
    settings = RunSettings(
        llm_provider="deepseek",
        quick_model="deepseek-v4-flash",
        deep_model="deepseek-v4-pro",
        quick_reasoning_effort="high",
        deep_reasoning_effort="max",
        data_config=build_default_config({}),
    )

    create_run_llms(settings)

    assert [call["reasoning_effort"] for call in calls] == ["high", "max"]
