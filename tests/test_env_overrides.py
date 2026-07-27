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
    assert config["max_debate_rounds"] == 1
    assert config["checkpoint_enabled"] is False
    assert config["provenance_appendix"] is False
    assert config["news_article_limit"] == 30
    assert config["sentiment_filing_limit"] == 20
    assert config["memory_log_max_entries"] == 1000
    assert config["memory_cross_ticker_limit"] == 3


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
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="3",
        TRADINGAGENTS_MAX_RISK_ROUNDS="2",
        TRADINGAGENTS_TICKER_NEWS_LOOKBACK_DAYS="14",
        TRADINGAGENTS_SOCIAL_LOOKBACK_DAYS="7",
        TRADINGAGENTS_MEMORY_LOG_MAX_ENTRIES="0",
        TRADINGAGENTS_MEMORY_CROSS_TICKER_LIMIT="5",
    )

    assert config["max_debate_rounds"] == 3
    assert isinstance(config["max_debate_rounds"], int)
    assert config["max_risk_discuss_rounds"] == 2
    assert isinstance(config["max_risk_discuss_rounds"], int)
    assert config["ticker_news_lookback_days"] == 14
    assert config["social_lookback_days"] == 7
    assert config["memory_log_max_entries"] == 0
    assert config["memory_cross_ticker_limit"] == 5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_bool_coercion(raw: str, expected: bool):
    config = _config(
        TRADINGAGENTS_CHECKPOINT_ENABLED=raw,
        TRADINGAGENTS_PROVENANCE_APPENDIX=raw,
    )

    assert config["checkpoint_enabled"] is expected
    assert config["provenance_appendix"] is expected


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
        TRADINGAGENTS_MAX_DEBATE_ROUNDS="",
    )

    assert config["llm_provider"] == "openai"
    assert config["max_debate_rounds"] == 1


def test_invalid_int_raises():
    with pytest.raises(ValueError, match="TRADINGAGENTS_MAX_DEBATE_ROUNDS"):
        _config(TRADINGAGENTS_MAX_DEBATE_ROUNDS="not-a-number")


@pytest.mark.parametrize(
    "env_name",
    [
        "TRADINGAGENTS_MEMORY_LOG_MAX_ENTRIES",
        "TRADINGAGENTS_MEMORY_CROSS_TICKER_LIMIT",
    ],
)
@pytest.mark.parametrize("value", ["-1", "not-a-number"])
def test_invalid_memory_limit_raises(env_name: str, value: str):
    with pytest.raises(ValueError, match=env_name):
        build_default_config({env_name: value})


@pytest.mark.parametrize("bad", ["treu", "flase", "maybe", "2", "enabled"])
def test_invalid_bool_raises(bad: str):
    with pytest.raises(ValueError, match="TRADINGAGENTS_CHECKPOINT_ENABLED"):
        _config(TRADINGAGENTS_CHECKPOINT_ENABLED=bad)


def test_invalid_provenance_bool_raises():
    with pytest.raises(ValueError, match="TRADINGAGENTS_PROVENANCE_APPENDIX"):
        _config(TRADINGAGENTS_PROVENANCE_APPENDIX="sometimes")


def test_unknown_env_var_is_ignored():
    config = _config(TRADINGAGENTS_NONEXISTENT_KEY="oops")

    assert "nonexistent_key" not in config
