"""yfinance news must not leak future-dated (or undated, in a backtest) articles
into a historical window.

Regressions for #992 (flat articles bypassed the date filter), #1007 (global
news injected future articles), #993 (empty-after-filter returned a blank body),
and #1126 (inclusive upper bound leaked the midnight-after article; host-local
timestamp parsing made filtering machine-dependent).
"""
from datetime import UTC, datetime

import pytest

import tradingagents.dataflows.yfinance_news as ynews


def _epoch(date_str):
    """Epoch seconds for UTC midnight of ``date_str`` (host-timezone independent)."""
    return int(
        datetime.strptime(date_str, "%Y-%m-%d")
        .replace(tzinfo=UTC)
        .timestamp()
    )


@pytest.mark.unit
def test_flat_article_publish_time_is_parsed():
    # #992: flat articles now carry a pub_date (was always None -> unfilterable).
    # #1126: parsed as UTC-aware, so the date can't shift with the host timezone.
    data = ynews._extract_article_data(
        {"title": "X", "publisher": "P", "link": "l", "providerPublishTime": _epoch("2025-05-09")}
    )
    assert data["pub_date"] is not None
    assert data["pub_date"].tzinfo is not None
    assert data["pub_date"] == datetime(2025, 5, 9, tzinfo=UTC)


@pytest.mark.unit
def test_stock_window_uses_new_york_calendar_not_utc_date():
    start = datetime(2025, 5, 9)
    end = datetime(2025, 5, 9)
    late_friday_in_new_york = datetime(2025, 5, 10, 3, 30, tzinfo=UTC)
    saturday_in_new_york = datetime(2025, 5, 10, 4, 0, tzinfo=UTC)

    assert ynews._in_news_window(
        late_friday_in_new_york, start, end, ticker="NVDA"
    )
    assert not ynews._in_news_window(
        saturday_in_new_york, start, end, ticker="NVDA"
    )


@pytest.mark.unit
def test_global_windows_use_utc_calendar():
    start = datetime(2025, 5, 9)
    end = datetime(2025, 5, 9)
    next_utc_day = datetime(2025, 5, 10, tzinfo=UTC)

    assert not ynews._in_news_window(next_utc_day, start, end)

    first_hour_of_tokyo_date = datetime(2025, 5, 8, 15, 30, tzinfo=UTC)
    assert ynews._in_news_window(
        first_hour_of_tokyo_date, start, end, ticker="9984.T"
    )


@pytest.mark.unit
def test_hong_kong_window_uses_local_calendar_not_new_york():
    start = datetime(2025, 5, 9)
    end = datetime(2025, 5, 9)
    first_hour_of_next_hong_kong_date = datetime(
        2025, 5, 9, 16, 30, tzinfo=UTC
    )

    assert not ynews._in_news_window(
        first_hour_of_next_hong_kong_date,
        start,
        end,
        ticker="0700.HK",
    )


@pytest.mark.unit
def test_window_excludes_future_and_undated_in_backtest():
    start = datetime(2025, 5, 1)
    end = datetime(2025, 5, 9)  # historical window (well in the past)
    inside = datetime(2025, 5, 5)
    future = datetime(2025, 6, 1)
    assert ynews._in_news_window(inside, start, end) is True
    assert ynews._in_news_window(future, start, end) is False     # look-ahead blocked
    assert ynews._in_news_window(None, start, end) is False        # undated -> excluded in backtest


@pytest.mark.unit
def test_window_keeps_undated_in_live_window():
    # Live window (reaches today): undated articles can't be "future", so keep them.
    now = datetime.now(UTC)
    assert ynews._in_news_window(None, now, now) is True


@pytest.mark.unit
def test_upper_bound_is_exclusive():
    # #1126: an article stamped exactly midnight AFTER end_date leaked in under
    # the old inclusive bound; the whole of end_date itself must still be kept.
    start = datetime(2025, 5, 1)
    end = datetime(2025, 5, 9)
    midnight_after = datetime(2025, 5, 10, 0, 0, 0, tzinfo=UTC)
    last_moment = datetime(2025, 5, 9, 23, 59, 59, tzinfo=UTC)
    assert ynews._in_news_window(midnight_after, start, end) is False
    assert ynews._in_news_window(last_moment, start, end) is True


@pytest.mark.unit
def test_offset_aware_timestamp_is_converted_not_truncated():
    # #1126: 2025-05-10T01:00+05:00 is really 2025-05-09T20:00Z -> inside the
    # window. Stripping tzinfo (old behavior) misread it as 05-10 and dropped it.
    start = datetime(2025, 5, 1)
    end = datetime(2025, 5, 9)
    aware = datetime.fromisoformat("2025-05-10T01:00:00+05:00")
    assert ynews._in_news_window(aware, start, end) is True


@pytest.mark.unit
def test_global_news_future_flat_article_excluded(monkeypatch):
    # #1007: a flat, future-dated global article must not appear in a historical run.
    future_article = {"title": "FUTURE EVENT", "publisher": "P", "link": "l",
                      "providerPublishTime": _epoch("2025-06-01")}
    past_article = {"title": "PAST EVENT", "publisher": "P", "link": "l",
                    "providerPublishTime": _epoch("2025-05-05")}

    class FakeSearch:
        def __init__(self, *a, **k):
            self.news = [future_article, past_article]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
    assert "PAST EVENT" in out
    assert "FUTURE EVENT" not in out  # #1007


@pytest.mark.unit
def test_global_news_empty_after_filter_is_informative(monkeypatch):
    # #993: everything filtered out -> a clear message, not a blank-bodied report.
    only_future = {"title": "FUTURE", "publisher": "P", "link": "l",
                   "providerPublishTime": _epoch("2025-06-01")}

    class FakeSearch:
        def __init__(self, *a, **k):
            self.news = [only_future]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    out = ynews.get_global_news_yfinance("2025-05-09", look_back_days=7, limit=10)
    assert "No global news found" in out
    assert "###" not in out  # no empty article body
