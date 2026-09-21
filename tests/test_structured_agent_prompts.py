"""Analyst prompts must preserve their tool-use boundaries."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import tradingagents.research.analysts.sentiment_analyst as sentiment
from tests.factories import analyst_runtime
from tradingagents.research.prompts.constraints import NO_EXTERNAL_TOOLS


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
        sentiment,
        "route_to_vendor",
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
        },
        analyst_runtime(),
    )

    text = "\n".join(
        str(getattr(message, "content", message))
        for message in captured["prompt"]
    )
    assert NO_EXTERNAL_TOOLS in text
    assert "tool-call date ranges" not in text


@pytest.mark.unit
@pytest.mark.parametrize("role", ["market", "news"])
def test_tool_using_analysts_keep_immutable_date_guidance(monkeypatch, role):
    from tests.factories import captured_analyst_prompt

    prompt = captured_analyst_prompt(monkeypatch, role)
    assert "tool-call date ranges" in prompt
    assert "2026-01-15" in prompt


@pytest.mark.unit
def test_constraint_text_is_unambiguous():
    assert "do not call external tools" in NO_EXTERNAL_TOOLS.lower()
    assert "{" not in NO_EXTERNAL_TOOLS
    assert "}" not in NO_EXTERNAL_TOOLS
