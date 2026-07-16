"""J-Quants technical indicators: fetch OHLCV with enough warm-up history, then
reuse the shared, vendor-neutral stockstats renderer so the report matches the
yfinance path exactly."""

from __future__ import annotations

from datetime import datetime

from dateutil.relativedelta import relativedelta

from ..market_data_validator import render_verified_market_snapshot
from ..stockstats_utils import render_indicator_window
from .jquants_stock import _fetch_ohlcv_frame

# Warm-up history fetched before the requested window so long indicators (e.g.
# the 200 SMA) have enough lookback. 200 trading days ~ 290 calendar days; pad
# generously to cover holidays/weekends.
_WARMUP_DAYS = 400


def get_indicator(
    symbol: str, indicator: str, curr_date: str, look_back_days: int
) -> str:
    """Return a date->value window for ``indicator`` ending at ``curr_date``."""
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d")
        - relativedelta(days=look_back_days + _WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_frame(symbol, start, curr_date)
    return render_indicator_window(df, indicator, curr_date, look_back_days)


def get_verified_market_snapshot(
    symbol: str, curr_date: str, look_back_days: int = 30
) -> str:
    """Return a J-Quants-backed deterministic market snapshot."""
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d")
        - relativedelta(days=look_back_days + _WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_frame(symbol, start, curr_date)
    return render_verified_market_snapshot(
        df,
        symbol,
        curr_date,
        look_back_days,
        source="J-Quants",
    )
