"""The canonical client key is mapped to the Google SDK parameter."""

from unittest.mock import patch

from tradingagents.llm.google_client import GoogleClient


def test_api_key_is_mapped_to_google_sdk():
    with patch("tradingagents.llm.google_client.NormalizedChatGoogleGenerativeAI") as constructor:
        GoogleClient("gemini-3.5-flash", api_key="test-key").get_llm()
    assert constructor.call_args.kwargs["google_api_key"] == "test-key"
