from unittest.mock import MagicMock, patch

import pytest

from tradingagents.credentials import use_credentials
from tradingagents.llm_clients.google_client import GoogleClient


def test_google_db_auth_ignores_ambient_vertex_backend(monkeypatch):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-test-key")
    with patch("tradingagents.llm_clients.google_client.Client", return_value=MagicMock()) as sdk:
        GoogleClient(
            "gemini-3.5-flash",
            api_key="db-test-key",
            base_url="https://generativelanguage.googleapis.com",
        ).get_llm()
    assert sdk.call_args.kwargs.get("api_key") == "db-test-key"
    assert sdk.call_args.kwargs.get("vertexai") is False
    assert (
        sdk.call_args.kwargs["http_options"].base_url == "https://generativelanguage.googleapis.com"
    )


def test_google_missing_db_key_does_not_use_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-test-key")
    with use_credentials({}), pytest.raises(ValueError, match="Configure the Google credential"):
        GoogleClient("gemini-3.5-flash").get_llm()
