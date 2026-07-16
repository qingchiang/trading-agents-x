from datetime import datetime, timedelta
from io import StringIO

import pandas as pd

from .alpha_vantage_common import _filter_csv_by_date_range, _make_api_request


def get_stock(
    symbol: str,
    start_date: str,
    end_date: str
) -> str:
    """
    Returns raw daily OHLCV values, adjusted close values, and historical split/dividend events
    filtered to the specified date range.

    Args:
        symbol: The name of the equity. For example: symbol=IBM
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        CSV string containing the daily adjusted time series data filtered to the date range.
    """
    # Parse dates to determine the range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    today = datetime.now()

    # Choose outputsize based on whether the requested range is within the latest 100 days
    # Compact returns latest 100 data points, so check if start_date is recent enough
    days_from_today_to_start = (today - start_dt).days
    outputsize = "compact" if days_from_today_to_start < 100 else "full"

    params = {
        "symbol": symbol,
        "outputsize": outputsize,
        "datatype": "csv",
    }

    response = _make_api_request("TIME_SERIES_DAILY_ADJUSTED", params)

    return _filter_csv_by_date_range(response, start_date, end_date)


def get_verified_market_snapshot(
    symbol: str, curr_date: str, look_back_days: int = 30
) -> str:
    """Return an Alpha Vantage-backed deterministic market snapshot."""
    # Match the J-Quants indicator warm-up: 400 calendar days plus the rendered
    # window is enough for the snapshot's longest (200-session) indicator.
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = (end - timedelta(days=look_back_days + 400)).strftime("%Y-%m-%d")
    raw = get_stock(symbol, start, curr_date)
    frame = pd.read_csv(StringIO(raw))
    columns = {str(c).strip().lower(): c for c in frame.columns}
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required <= columns.keys():
        raise ValueError(
            f"Alpha Vantage snapshot response lacks OHLCV columns for {symbol}."
        )

    out = pd.DataFrame({
        "Date": frame[columns["timestamp"]],
        "Open": frame[columns["open"]],
        "High": frame[columns["high"]],
        "Low": frame[columns["low"]],
        "Close": frame[columns["close"]],
        "Volume": frame[columns["volume"]],
    })
    # TIME_SERIES_DAILY_ADJUSTED exposes only adjusted close. Scale O/H/L by
    # the same factor so all four price fields share one corporate-action basis.
    adjusted = columns.get("adjusted_close")
    if adjusted is not None:
        close = pd.to_numeric(out["Close"], errors="coerce")
        factor = pd.to_numeric(frame[adjusted], errors="coerce") / close
        for col in ("Open", "High", "Low", "Close"):
            out[col] = pd.to_numeric(out[col], errors="coerce") * factor

    from .market_data_validator import render_verified_market_snapshot

    return render_verified_market_snapshot(
        out,
        symbol,
        curr_date,
        look_back_days,
        source="Alpha Vantage",
    )
