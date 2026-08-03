"""Tests for the canonical provider-to-environment-variable mapping."""

from __future__ import annotations

import pytest

from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV, get_api_key_env
from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY

# ---- Mapping coverage -----------------------------------------------------


def test_every_advertised_provider_has_an_api_key_policy():
    """The Web capabilities catalog must not advertise an unknown key policy."""
    assert set(PROVIDER_REGISTRY).issubset(PROVIDER_API_KEY_ENV)


@pytest.mark.parametrize(
    "provider,env_var",
    [
        ("openai",     "OPENAI_API_KEY"),
        ("anthropic",  "ANTHROPIC_API_KEY"),
        ("google",     "GOOGLE_API_KEY"),
        ("azure",      "AZURE_OPENAI_API_KEY"),
        ("xai",        "XAI_API_KEY"),
        ("deepseek",   "DEEPSEEK_API_KEY"),
        ("qwen",       "DASHSCOPE_API_KEY"),
        ("qwen-cn",    "DASHSCOPE_CN_API_KEY"),
        ("glm",        "ZHIPU_API_KEY"),
        ("glm-cn",     "ZHIPU_CN_API_KEY"),
        ("minimax",    "MINIMAX_API_KEY"),
        ("minimax-cn", "MINIMAX_CN_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ],
)
def test_known_providers_resolve(provider, env_var):
    assert get_api_key_env(provider) == env_var


def test_ollama_has_no_key():
    assert get_api_key_env("ollama") is None


def test_unknown_provider_returns_none():
    assert get_api_key_env("not-a-real-provider") is None


def test_case_insensitive_lookup():
    assert get_api_key_env("OpenAI") == "OPENAI_API_KEY"
    assert get_api_key_env("QWEN-CN") == "DASHSCOPE_CN_API_KEY"
