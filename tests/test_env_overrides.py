"""Tests for explicit TRADINGAGENTS_* overlays at application boundaries."""

from __future__ import annotations

import pytest

from tradingagents.default_config import build_default_config


def _config(**overrides):
    return build_default_config(overrides)


def test_no_env_uses_built_in_defaults():
    config = _config()

    assert config["llm_provider"] == "openai"
    assert config["deep_think_llm"] == "gpt-5.5"
    assert config["quick_think_llm"] == "gpt-5.4-mini"
    assert config["backend_url"] is None
    assert "provenance_appendix" not in config
    assert config["news_article_limit"] == 30
    assert config["sentiment_filing_limit"] == 20


def test_string_overrides():
    config = _config(
        TRADINGAGENTS_LLM_PROVIDER="google",
        TRADINGAGENTS_DEEP_THINK_LLM="gemini-3-pro-preview",
        TRADINGAGENTS_QUICK_THINK_LLM="gemini-3-flash-preview",
        TRADINGAGENTS_LLM_BACKEND_URL="https://example.invalid/v1",
        TRADINGAGENTS_OUTPUT_LANGUAGE="Chinese",
    )

    assert config["llm_provider"] == "google"
    assert config["deep_think_llm"] == "gemini-3-pro-preview"
    assert config["quick_think_llm"] == "gemini-3-flash-preview"
    assert config["backend_url"] == "https://example.invalid/v1"
    assert config["output_language"] == "Chinese"


def test_int_coercion():
    config = _config(
        TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="14",
        TRADINGAGENTS_SOCIAL_LOOKBACK_DAYS="7",
    )

    assert config["ticker_news_lookback_days"] == 14
    assert config["social_lookback_days"] == 7


def test_reasoning_thinking_overrides():
    config = _config(
        TRADINGAGENTS_OPENAI_REASONING_EFFORT="high",
        TRADINGAGENTS_GOOGLE_THINKING_LEVEL="minimal",
        TRADINGAGENTS_ANTHROPIC_EFFORT="low",
        TRADINGAGENTS_QUICK_REASONING_EFFORT=" MAX ",
        TRADINGAGENTS_DEEP_REASONING_EFFORT="provider_default",
    )

    assert config["openai_reasoning_effort"] == "high"
    assert config["google_thinking_level"] == "minimal"
    assert config["anthropic_effort"] == "low"
    assert config["quick_reasoning_effort"] == " MAX "
    assert config["deep_reasoning_effort"] == "provider_default"


def test_reasoning_effort_defaults_to_none():
    config = _config()

    assert config["openai_reasoning_effort"] is None
    assert config["google_thinking_level"] is None
    assert config["anthropic_effort"] is None
    assert config["quick_reasoning_effort"] is None
    assert config["deep_reasoning_effort"] is None


def test_empty_env_value_does_not_clobber_default():
    config = _config(
        TRADINGAGENTS_LLM_PROVIDER="",
        TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="",
    )

    assert config["llm_provider"] == "openai"
    assert config["ticker_news_lookback_days"] == 14


def test_invalid_int_raises():
    with pytest.raises(
        ValueError,
        match="TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS",
    ):
        _config(TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="not-a-number")


@pytest.mark.parametrize(
    "env_name",
    [
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS",
        "TRADINGAGENTS_MAX_RISK_ROUNDS",
        "TRADINGAGENTS_CHECKPOINT_ENABLED",
        "TRADINGAGENTS_MEMORY_LOG_MAX_ENTRIES",
        "TRADINGAGENTS_MEMORY_CROSS_TICKER_LIMIT",
        "TRADINGAGENTS_PROVENANCE_APPENDIX",
    ],
)
def test_removed_legacy_environment_keys_are_ignored(env_name: str):
    config = build_default_config({env_name: "legacy-value"})

    assert "checkpoint_enabled" not in config
    assert "memory_log_max_entries" not in config
    assert "memory_cross_ticker_limit" not in config
    assert "max_debate_rounds" not in config
    assert "max_risk_discuss_rounds" not in config
    assert "provenance_appendix" not in config


def test_unknown_env_var_is_ignored():
    config = _config(TRADINGAGENTS_NONEXISTENT_KEY="oops")

    assert "nonexistent_key" not in config
