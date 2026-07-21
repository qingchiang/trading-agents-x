"""Japan macro primary/fallback and market-cutoff tests."""

from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows import jp_macro
from tradingagents.dataflows.cn.common import AkShareSchemaError
from tradingagents.dataflows.jp.calendar import completed_market_date


@pytest.fixture(autouse=True)
def clear_cache():
    jp_macro._series_cache.clear()
    yield
    jp_macro._series_cache.clear()


@pytest.mark.unit
def test_static_quote_id_returns_validated_daily_points(monkeypatch):
    monkeypatch.setattr(
        jp_macro,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {
                "code": "JP10Y",
                "market": 171,
                "name": "日本10年期国债",
                "klines": ["2026-07-16,2.6,2.7", "2026-07-18,9,9"],
            }
        },
    )
    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-17", 10)
    assert data["points"] == [("2026-07-16", "2.7")]
    assert data["actual_source"] == "Eastmoney"
    assert data["frequency"] == "Daily"
    assert "fallback_reason" not in data


@pytest.mark.unit
@pytest.mark.parametrize("value", ["NaN", "inf", "-inf"])
def test_non_finite_yield_is_rejected(monkeypatch, value):
    monkeypatch.setattr(
        jp_macro,
        "_request_json",
        lambda *_args, **_kwargs: {
            "data": {
                "code": "JP10Y",
                "market": 171,
                "name": "日本10年期国债",
                "klines": [f"2026-07-16,2.6,{value}"],
            }
        },
    )

    with pytest.raises(AkShareSchemaError, match="non-finite"):
        jp_macro._fetch_eastmoney("171.JP10Y", date(2026, 7, 1), date(2026, 7, 17))


@pytest.mark.unit
def test_static_failure_resolves_dynamic_quote_id(monkeypatch):
    calls = []

    def fetch(quote_id, *_args):
        calls.append(quote_id)
        if len(calls) == 1:
            raise AkShareSchemaError("stale mapping")
        return [("2026-07-17", "2.7")]

    monkeypatch.setattr(jp_macro, "_fetch_eastmoney", fetch)
    monkeypatch.setattr(jp_macro, "_resolve_quote_id", lambda: "171.JP10Y")
    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-17", 10)
    assert calls == ["171.JP10Y", "171.JP10Y"]
    assert data["points"] == [("2026-07-17", "2.7")]


@pytest.mark.unit
def test_self_heal_failure_falls_back_to_fred(monkeypatch):
    monkeypatch.setattr(
        jp_macro, "_fetch_primary", mock.Mock(side_effect=AkShareSchemaError("down"))
    )
    monkeypatch.setattr(
        jp_macro.fred,
        "fetch_series",
        lambda indicator, curr_date, look_back_days: {
            "series_id": indicator,
            "title": "old",
            "units": "%",
            "frequency": "Monthly",
            "seasonal": "",
            "start_date": "2025-07-01",
            "points": [("2026-06-01", "1.9")],
        },
    )
    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-17", 365)
    assert data["actual_source"] == "FRED"
    assert data["frequency"] == "Monthly"
    assert "fallback" in data["timing"]
    assert data["fallback_reason"] == "Eastmoney primary retrieval unavailable"


@pytest.mark.unit
def test_empty_self_heal_result_falls_back_to_fred(monkeypatch):
    monkeypatch.setattr(jp_macro, "_fetch_primary", lambda *_args: [])
    fallback = {
        "series_id": "IRLTLT01JPM156N",
        "title": "old",
        "units": "%",
        "frequency": "Monthly",
        "seasonal": "",
        "start_date": "2025-07-01",
        "points": [("2026-06-01", "1.9")],
    }
    monkeypatch.setattr(jp_macro.fred, "fetch_series", lambda *_args: fallback)

    data = jp_macro.fetch_series("jp_10y_yield", "2026-07-17", 365)

    assert data["actual_source"] == "FRED"
    assert data["fallback_reason"] == "Eastmoney returned no usable observations"


@pytest.mark.unit
def test_today_before_17_excludes_incomplete_value():
    tokyo = ZoneInfo("Asia/Tokyo")
    assert completed_market_date(
        date(2026, 7, 21), datetime(2026, 7, 21, 16, 59, tzinfo=tokyo)
    ) == date(2026, 7, 17)
    assert completed_market_date(
        date(2026, 7, 21), datetime(2026, 7, 21, 17, 0, tzinfo=tokyo)
    ) == date(2026, 7, 21)
