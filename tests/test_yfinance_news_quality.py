"""Yahoo ticker-news integration tests for evidence filtering and rendering."""

import time
from datetime import datetime
from urllib.error import HTTPError

import pytest
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows import yfinance_news as ynews
from tradingagents.dataflows.errors import VendorRateLimitError
from tradingagents.dataflows.rate_limit import stop_on_rate_limit_scope


def _epoch(date_str: str) -> int:
    return int(time.mktime(datetime.strptime(date_str, "%Y-%m-%d").timetuple()))


def _article(title: str, summary: str = "", date: str = "2025-05-05") -> dict:
    return {
        "title": title,
        "summary": summary,
        "publisher": "Example",
        "link": "https://example.test/article",
        "providerPublishTime": _epoch(date),
    }


def _run(monkeypatch, articles, *, limit=10, ticker="NVDA", identity=None):
    seen = {}

    class FakeTicker:
        def __init__(self, symbol):
            seen["symbol"] = symbol

        def get_news(self, count):
            seen["count"] = count
            return articles

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ynews, "yf_retry", lambda fn, **_kwargs: fn())
    monkeypatch.setattr(
        ynews,
        "resolve_search_identity",
        lambda symbol: (
            {"company_name": "NVIDIA Corporation"}
            if identity is None
            else identity
        ),
    )
    monkeypatch.setattr(ynews, "get_config", lambda: {"news_article_limit": limit})
    result = ynews.get_news_yfinance(ticker, "2025-05-01", "2025-05-09")
    return result, seen


@pytest.mark.unit
def test_renderer_exposes_direct_candidate_and_context_tiers(monkeypatch):
    out, _ = _run(monkeypatch, [
        _article("NVIDIA Corporation launches a new accelerator"),
        _article("NVIDIA launches a new accelerator"),
        _article("Chip supply improves", "NVIDIA may benefit from the change."),
        _article("NVDA Covered Call ETF raises distribution"),
    ])

    assert "### [direct] NVIDIA Corporation launches" in out
    assert "### [candidate] NVIDIA launches" in out
    assert "### [candidate] Chip supply improves" in out
    assert "### [context] NVDA Covered Call ETF" in out
    assert "kept=4 (direct=1, candidate=2, context=1)" in out


@pytest.mark.unit
def test_renderer_preserves_yahoo_exact_publication_timestamp(monkeypatch):
    out, _ = _run(monkeypatch, [{
        "content": {
            "title": "NVIDIA Corporation launches a new accelerator",
            "summary": "details",
            "provider": {"displayName": "Example"},
            "canonicalUrl": {"url": "https://example.test/article"},
            "pubDate": "2025-05-05T14:30:00Z",
        }
    }])
    assert "Published: 2025-05-05T14:30:00Z" in out


@pytest.mark.unit
def test_quality_dedupe_and_limit_run_after_date_filter(monkeypatch):
    articles = [_article(f"Unrelated story {i}") for i in range(17)] + [
        _article("NVIDIA future event", date="2025-06-01"),
        _article("NVIDIA Corporation announces investment A"),
        _article("NVIDIA Corporation announces investment A"),
        _article("(NVDA) announces investment B"),
    ]
    out, seen = _run(monkeypatch, articles, limit=2)

    assert seen["count"] == 200
    assert "future event" not in out
    assert out.count("### [direct]") == 2
    assert "candidates=20; relevant=2; kept=2" in out
    assert "dropped=18; omitted_by_limit=0" in out


@pytest.mark.unit
def test_all_irrelevant_candidates_return_explicit_no_relevant(monkeypatch):
    out, _ = _run(monkeypatch, [
        _article("SpaceX-linked ETF rallies after launch"),
        _article("ASML outlines its next lithography platform"),
    ])
    assert "No relevant news found for NVDA" in out
    assert "after quality filtering (2 in-window candidates dropped)" in out


@pytest.mark.unit
def test_search_failure_keeps_raw_and_canonical_ticker_candidates(monkeypatch):
    out, seen = _run(
        monkeypatch,
        [_article("GC=F futures rise after inflation data")],
        ticker="XAUUSD",
        identity={},
    )
    assert seen["symbol"] == "GC=F"
    assert "### [candidate] GC=F futures rise" in out


@pytest.mark.unit
def test_derived_search_brand_is_forwarded_as_candidate(monkeypatch):
    out, _ = _run(
        monkeypatch,
        [_article("Amazon launches a new AI service")],
        ticker="AMZN",
        identity={"company_name": "Amazon.com, Inc."},
    )
    assert "### [candidate] Amazon launches a new AI service" in out


@pytest.mark.unit
def test_yahoo_http_429_surfaces_typed_rate_limit_without_rendering_error(monkeypatch):
    class FakeTicker:
        def __init__(self, _symbol):
            pass

        def get_news(self, count):
            raise HTTPError("url", 429, "slow down", {}, None)

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ynews, "yf_retry", lambda fn, **_kwargs: fn())
    monkeypatch.setattr(ynews, "resolve_search_identity", lambda _symbol: {})
    monkeypatch.setattr(ynews, "get_config", lambda: {"news_article_limit": 10})

    with pytest.raises(VendorRateLimitError, match="Yahoo Finance rate limited"):
        ynews.get_news_yfinance("NVDA", "2025-05-01", "2025-05-09")


@pytest.mark.unit
def test_focused_yahoo_rate_limit_does_not_retry(monkeypatch):
    calls = []

    class FakeTicker:
        def __init__(self, _symbol):
            pass

        def get_news(self, count):
            calls.append(count)
            raise YFRateLimitError()

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ynews, "resolve_search_identity", lambda _symbol: {})
    monkeypatch.setattr(ynews, "get_config", lambda: {"news_article_limit": 10})

    with stop_on_rate_limit_scope(True), pytest.raises(VendorRateLimitError):
        ynews.get_news_yfinance("NVDA", "2025-05-01", "2025-05-09")
    assert calls == [200]
