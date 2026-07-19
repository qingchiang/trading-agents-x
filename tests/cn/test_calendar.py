"""Mainland China trading-calendar boundaries."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows.cn import calendar
from tradingagents.dataflows.cn.common import AkShareSchemaError


@pytest.mark.unit
def test_previous_trade_date_rolls_weekend_and_holiday(monkeypatch):
    dates = (
        date(2026, 4, 3),
        date(2026, 4, 7),
        date(2026, 4, 8),
    )
    monkeypatch.setattr(calendar, "trading_dates", lambda: dates)

    assert calendar.previous_trade_date("2026-04-04") == date(2026, 4, 3)
    assert calendar.previous_trade_date("2026-04-06") == date(2026, 4, 3)
    assert calendar.previous_trade_date("2026-04-07") == date(2026, 4, 7)
    assert calendar.is_trade_date("2026-04-06") is False


@pytest.mark.unit
def test_exclusive_previous_trade_date(monkeypatch):
    dates = (date(2026, 4, 3), date(2026, 4, 7))
    monkeypatch.setattr(calendar, "trading_dates", lambda: dates)
    assert calendar.previous_trade_date("2026-04-07", inclusive=False) == date(
        2026, 4, 3
    )


@pytest.mark.unit
def test_calendar_refuses_dates_beyond_its_coverage(monkeypatch):
    monkeypatch.setattr(calendar, "trading_dates", lambda: (date(2026, 4, 3),))
    with pytest.raises(AkShareSchemaError, match="calendar ends"):
        calendar.previous_trade_date("2026-04-04")


@pytest.mark.unit
def test_live_trading_day_uses_prior_session_before_daily_bar_settles(monkeypatch):
    dates = (date(2026, 7, 16), date(2026, 7, 17))
    monkeypatch.setattr(calendar, "trading_dates", lambda: dates)
    before_settle = datetime(
        2026, 7, 17, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    after_settle = datetime(
        2026, 7, 17, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )

    assert calendar.effective_trade_date("2026-07-17", now=before_settle) == date(
        2026, 7, 16
    )
    assert calendar.effective_trade_date("2026-07-17", now=after_settle) == date(
        2026, 7, 17
    )
