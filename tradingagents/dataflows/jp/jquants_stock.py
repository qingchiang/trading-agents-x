"""J-Quants daily OHLCV: a DataFrame for indicators and a CSV string for the
market analyst's raw price view."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from ..stockstats_utils import _assert_ohlcv_not_stale, _clean_dataframe
from ..symbol_utils import NoMarketDataError
from .jquants_common import from_jquants_code, memoized_fetch, to_jquants_code

# Process-local cache of fetched daily bars keyed by (code, from, to). The
# get_indicators tool calls this vendor once per requested indicator over the
# same window, so without this each indicator would re-hit the rate-limited
# J-Quants API for identical data. Caching the raw records (not the DataFrame)
# keeps each caller's frame independent of stockstats' in-place wrapping.
_records_cache: dict[tuple[str, str, str], list[dict]] = {}


def _fetch_daily_bars(code: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch daily bars for ``code`` over the range, memoized per (code, from, to)."""
    return memoized_fetch(
        _records_cache, (code, start_date, end_date),
        "/equities/bars/daily",
        {"code": code, "from": start_date, "to": end_date}, "data",
    )

# J-Quants v2 /equities/bars/daily carries both raw (O/H/L/C/Vo) and
# split/dividend-adjusted (AdjO/AdjH/AdjL/AdjC/AdjVo) prices. Prefer adjusted
# (consistent with indicators across corporate actions); fall back to raw when
# an adjusted field is missing/null. Each entry maps our column -> (adjusted, raw).
_PRICE_FIELDS = {
    "Open": ("AdjO", "O"),
    "High": ("AdjH", "H"),
    "Low": ("AdjL", "L"),
    "Close": ("AdjC", "C"),
    "Volume": ("AdjVo", "Vo"),
}


def _pick(record: dict, adjusted_key: str, raw_key: str):
    """Return the adjusted value when present and non-null, else the raw value."""
    value = record.get(adjusted_key)
    return value if value is not None else record.get(raw_key)


def _fetch_ohlcv_frame(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Return a cleaned OHLCV frame (Date + Open/High/Low/Close/Volume) for the
    requested range. Raises NoMarketDataError when J-Quants has no usable rows."""
    code = to_jquants_code(symbol)
    canonical = from_jquants_code(code)
    records = _fetch_daily_bars(code, start_date, end_date)
    if not records:
        raise NoMarketDataError(
            symbol, canonical, f"no rows between {start_date} and {end_date}"
        )

    adjusted_complete = all(
        record.get(adjusted) is not None
        for record in records
        for adjusted, _raw in _PRICE_FIELDS.values()
    )
    rows = [
        {
            "Date": r.get("Date"),
            **{col: _pick(r, adj, raw) for col, (adj, raw) in _PRICE_FIELDS.items()},
        }
        for r in records
    ]
    df = pd.DataFrame(rows)
    # Sort chronologically before cleaning so the ffill/bfill in _clean_dataframe
    # fills price gaps from the prior trading day, not row order. _clean_dataframe
    # parses Date, coerces numerics, drops NaN-Close rows, and ffill/bfills the
    # rest (shared with the yfinance path so both vendors clean identically).
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df = _clean_dataframe(df).reset_index(drop=True)
    if df.empty:
        raise NoMarketDataError(symbol, canonical, "no usable rows after parsing")
    # Reject a stale frame (latest row far older than the requested end) before
    # it reaches indicators or the agent, mirroring the yfinance path (#1021).
    _assert_ohlcv_not_stale(df, end_date, symbol, canonical)
    df.attrs["price_adjustment"] = (
        "J-Quants adjusted OHLCV v2"
        if adjusted_complete
        else "mixed adjusted/raw J-Quants OHLCV v2"
    )
    return df


def fetch_latest_daily_bar_date(symbol: str, start_date: str, end_date: str) -> date:
    """Return the latest actual J-Quants daily-bar date in a requested window."""
    return _fetch_ohlcv_frame(symbol, start_date, end_date).iloc[-1]["Date"].date()


# TOPIX index daily OHLC (/indices/bars/daily/topix): the market-portfolio proxy
# for Japanese beta. Available on the Light plan; carries O/H/L/C only (an index
# has no volume and no securities code). Cached per (from, to).
_topix_cache: dict[tuple[str, str], list[dict]] = {}


def fetch_topix_closes(start_date: str, end_date: str) -> pd.DataFrame:
    """Return a ``Date``/``Close`` frame of the TOPIX index over the range.

    TOPIX (cap-weighted, whole-market) is the market portfolio used for Japanese
    beta, versus the price-weighted Nikkei 225. The range is caller-bounded, so
    passing ``end_date = curr_date`` keeps it look-ahead safe. Raises
    NoMarketDataError when J-Quants returns no usable rows for the range.
    """
    records = memoized_fetch(
        _topix_cache, (start_date, end_date),
        "/indices/bars/daily/topix", {"from": start_date, "to": end_date}, "data",
    )
    if not records:
        raise NoMarketDataError(
            "TOPIX", "TOPIX", f"no index rows between {start_date} and {end_date}"
        )
    # Reuse the shared OHLCV cleaner (parse Date, coerce numerics, drop NaN-Close)
    # so index and equity frames clean identically; it only touches the columns
    # present, so a Date/Close frame is fine.
    df = pd.DataFrame([{"Date": r.get("Date"), "Close": r.get("C")} for r in records])
    df = _clean_dataframe(df).sort_values("Date").reset_index(drop=True)
    if df.empty:
        raise NoMarketDataError("TOPIX", "TOPIX", "no usable index rows after parsing")
    return df


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return daily OHLCV for ``symbol`` over the range as a CSV string."""
    df = _fetch_ohlcv_frame(symbol, start_date, end_date)
    canonical = from_jquants_code(to_jquants_code(symbol))

    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        out[col] = out[col].round(2)
    csv_string = out.to_csv(index=False)

    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"
    header = (
        f"# Stock data for {label} from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string
