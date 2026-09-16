"""Tests for OLLAMA_BASE_URL resolution in the provider client."""

from __future__ import annotations

import importlib


def _reload_client():
    import tradingagents.llm_clients.openai_client as mod
    return importlib.reload(mod)


def _base_url(mod, provider, **kwargs):
    return str(mod.OpenAIClient(model="m", provider=provider, **kwargs).get_llm().openai_api_base)


def test_resolver_returns_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    mod = _reload_client()
    assert _base_url(mod, "ollama") == "http://localhost:11434/v1"


def test_resolver_ignores_ambient_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://remote-ollama:11434/v1")
    mod = _reload_client()
    assert _base_url(mod, "ollama") == "http://localhost:11434/v1"


def test_ambient_changes_do_not_change_connection(monkeypatch):
    """Changing ambient environment cannot replace the resolved connection."""
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    mod = _reload_client()
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://late-set:11434/v1")
    assert _base_url(mod, "ollama") == "http://localhost:11434/v1"


def test_resolver_does_not_affect_other_providers(monkeypatch):
    """OLLAMA_BASE_URL should NOT leak into xai/deepseek/etc."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://elsewhere/v1")
    mod = _reload_client()
    assert _base_url(mod, "xai") == "https://api.x.ai/v1"
    assert _base_url(mod, "deepseek") == "https://api.deepseek.com"


def test_client_uses_explicit_connection(monkeypatch):
    """End-to-end: the client uses the explicit resolved endpoint."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://my-ollama:11434/v1")
    mod = _reload_client()
    client = mod.OpenAIClient(model="llama3.1", provider="ollama", base_url="http://my-ollama:11434/v1")
    llm = client.get_llm()
    assert "my-ollama" in str(llm.openai_api_base)


def test_explicit_base_url_overrides_env(monkeypatch):
    """An explicit base_url passed to the client wins over the env var."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-set:11434/v1")
    mod = _reload_client()
    client = mod.OpenAIClient(
        model="llama3.1",
        provider="ollama",
        base_url="http://explicit:11434/v1",
    )
    llm = client.get_llm()
    assert "explicit" in str(llm.openai_api_base)
    assert "env-set" not in str(llm.openai_api_base)
