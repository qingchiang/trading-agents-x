"""Guard the news analyst prompt against tool-signature drift (#1116).

The prompt used to advertise ``get_news(query, ...)`` while the tool takes a
``ticker``, tricking the LLM into hallucinating free-text query calls.
"""
import pytest

from tests.support.factories import captured_analyst_prompt
from tradingagents.research.tools.news_data_tools import get_news


@pytest.mark.unit
def test_news_prompt_matches_get_news_signature(monkeypatch):
    src = captured_analyst_prompt(monkeypatch, "news")
    graph_args = set(get_news.tool_call_schema.model_json_schema()["properties"])
    assert graph_args == {"ticker", "window"}
    assert "get_news(ticker, window)" in src
    assert "get_news(ticker, start_date, end_date)" not in src
    assert "get_news(query" not in src


@pytest.mark.unit
def test_news_prompt_preserves_evidence_boundaries(monkeypatch):
    src = captured_analyst_prompt(monkeypatch, "news")
    assert "`[direct]` has explicit ticker or full-name evidence" in src
    assert "`[candidate]` contains an ambiguous ticker/name" in src
    assert "Never assume relevance merely because Yahoo returned" in src
    assert "`[context]` is only an external driver" in src
