"""Japan MOF JP10Y primary, publication cutoff, and FRED fallback tests."""

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows import fred, jp_macro
from tradingagents.dataflows.jp import mof_yield

_TOKYO = ZoneInfo("Asia/Tokyo")


@pytest.fixture(autouse=True)
def clear_cache():
    jp_macro._series_cache.clear()
    mof_yield.clear_memory_cache()
    yield
    jp_macro._series_cache.clear()
    mof_yield.clear_memory_cache()


def _fred_fallback():
    return {
        "series_id": "IRLTLT01JPM156N",
        "title": "old",
        "units": "%",
        "frequency": "Monthly",
        "seasonal": "",
        "start_date": "2025-07-01",
        "points": [("2026-06-01", "1.9")],
    }


@pytest.mark.unit
def test_mof_daily_primary_records_official_source(monkeypatch):
    now = datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO)
    monkeypatch.setattr(mof_yield, "tokyo_now", lambda _now=None: now)
    monkeypatch.setattr(
        jp_macro,
        "_fetch_primary",
        lambda *_args, **_kwargs: [("2026-07-16", "2.719"), ("2026-07-17", "2.715")],
    )
    monkeypatch.setattr(
        fred,
        "fetch_series",
        mock.Mock(side_effect=AssertionError("FRED must not be called")),
    )

    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 10)

    assert data["points"][-1] == ("2026-07-17", "2.715")
    assert data["actual_source"] == "Japan Ministry of Finance"
    assert data["frequency"] == "Daily"
    assert "09:30 JST" in data["timing"]
    assert "fallback_reason" not in data


@pytest.mark.unit
def test_today_cache_is_split_at_0930(monkeypatch):
    clock = {"now": datetime(2026, 7, 21, 9, 29, tzinfo=_TOKYO)}
    monkeypatch.setattr(mof_yield, "tokyo_now", lambda _now=None: clock["now"])
    fetch = mock.Mock(
        side_effect=[
            [("2026-07-16", "2.719")],
            [("2026-07-16", "2.719"), ("2026-07-17", "2.715")],
        ]
    )
    monkeypatch.setattr(jp_macro, "_fetch_primary", fetch)

    before = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 10)
    clock["now"] = datetime(2026, 7, 21, 9, 30, tzinfo=_TOKYO)
    after = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 10)

    assert before["points"][-1][0] == "2026-07-16"
    assert after["points"][-1][0] == "2026-07-17"
    assert fetch.call_count == 2


@pytest.mark.unit
def test_mof_failure_falls_back_to_fred(monkeypatch):
    monkeypatch.setattr(
        mof_yield,
        "tokyo_now",
        lambda _now=None: datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO),
    )
    monkeypatch.setattr(
        jp_macro,
        "_fetch_primary",
        mock.Mock(side_effect=mof_yield.MofSchemaError("changed")),
    )
    monkeypatch.setattr(fred, "fetch_series", lambda *_args: _fred_fallback())

    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 365)

    assert data["actual_source"] == "FRED"
    assert data["frequency"] == "Monthly"
    assert data["fallback_reason"] == "MOF primary retrieval unavailable"


@pytest.mark.unit
def test_empty_mof_result_falls_back_with_distinct_reason(monkeypatch):
    monkeypatch.setattr(
        mof_yield,
        "tokyo_now",
        lambda _now=None: datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO),
    )
    monkeypatch.setattr(jp_macro, "_fetch_primary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fred, "fetch_series", lambda *_args: _fred_fallback())

    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 365)

    assert data["actual_source"] == "FRED"
    assert data["fallback_reason"] == "MOF returned no usable observations"


@pytest.mark.unit
def test_both_sources_unavailable_returns_none_without_caching(monkeypatch):
    monkeypatch.setattr(
        mof_yield,
        "tokyo_now",
        lambda _now=None: datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO),
    )
    primary = mock.Mock(side_effect=mof_yield.MofRequestError("down"))
    monkeypatch.setattr(jp_macro, "_fetch_primary", primary)
    monkeypatch.setattr(fred, "fetch_series", lambda *_args: None)

    assert jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 365) is None
    assert jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 365) is None
    assert primary.call_count == 2


@pytest.mark.unit
def test_mof_primary_succeeds_without_fred_key(monkeypatch):
    monkeypatch.setattr(
        mof_yield,
        "tokyo_now",
        lambda _now=None: datetime(2026, 7, 21, 10, 0, tzinfo=_TOKYO),
    )
    monkeypatch.setattr(
        jp_macro,
        "_fetch_primary",
        lambda *_args, **_kwargs: [("2026-07-17", "2.715")],
    )
    monkeypatch.setattr(
        fred,
        "fetch_series",
        mock.Mock(side_effect=fred.FredNotConfiguredError("no key")),
    )

    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-21", 10)

    assert data["actual_source"] == "Japan Ministry of Finance"
