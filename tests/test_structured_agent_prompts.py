"""Analyst prompts must preserve their tool-use boundaries."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

import tradingagents.agents.analysts.sentiment_analyst as sentiment
from tradingagents.agents.utils.structured import NO_EXTERNAL_TOOLS


@pytest.mark.unit
def test_sentiment_prompt_states_no_external_tool_constraint(monkeypatch):
    monkeypatch.setattr(
        sentiment,
        "fetch_stocktwits_messages",
        lambda *args, **kwargs: "stocktwits",
    )
    monkeypatch.setattr(
        sentiment,
        "fetch_reddit_posts",
        lambda *args, **kwargs: "reddit",
    )
    monkeypatch.setattr(
        sentiment.get_news,
        "func",
        lambda *args, **kwargs: "news",
        raising=False,
    )
    monkeypatch.setattr(sentiment, "is_near_live", lambda _date, _ticker: True)
    captured = {}
    llm = MagicMock()

    def invoke(prompt):
        captured["prompt"] = prompt
        return MagicMock(content="# Sentiment\n\nComplete draft.")

    llm.invoke.side_effect = invoke
    sentiment.create_sentiment_analyst(llm)(
        {
            "company_of_interest": "NVDA",
            "trade_date": "2026-01-15",
            "asset_type": "stock",
            "messages": [],
        }
    )

    text = "\n".join(
        str(getattr(message, "content", message))
        for message in captured["prompt"]
    )
    assert NO_EXTERNAL_TOOLS in text
    assert "tool-call date ranges" not in text


@pytest.mark.unit
def test_tool_using_analysts_keep_immutable_date_guidance():
    import tradingagents.agents.analysts.market_analyst as market
    import tradingagents.agents.analysts.news_analyst as news

    for module in (market, news):
        assert "tool-call date ranges" in inspect.getsource(module)


@pytest.mark.unit
def test_constraint_text_is_unambiguous():
    assert "do not call external tools" in NO_EXTERNAL_TOOLS.lower()
    assert "{" not in NO_EXTERNAL_TOOLS
    assert "}" not in NO_EXTERNAL_TOOLS
