"""J-Quants technical indicators: fetch OHLCV with enough warm-up history, then
reuse the shared, vendor-neutral stockstats renderer so the report matches the
yfinance path exactly."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

from tradingagents.provenance import (
    SourceInterval,
    SourceObservation,
    SourceWatermark,
    attach_source_observations,
    attach_source_watermarks,
)

from ..market_data_validator import render_verified_market_snapshot
from ..stockstats_utils import render_indicator_window
from .jquants_stock import _fetch_ohlcv_frame

# Warm-up history fetched before the requested window so long indicators (e.g.
# the 200 SMA) have enough lookback. 200 trading days ~ 290 calendar days; pad
# generously to cover holidays/weekends.
_WARMUP_DAYS = 400
_MIN_WARMUP_ROWS = 200
_MIN_WARMUP_SPAN_DAYS = 280
_TOKYO = ZoneInfo("Asia/Tokyo")
def get_indicator(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    """Return a date->value window for ``indicator`` ending at ``curr_date``."""
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days + _WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_frame(symbol, start, curr_date)
    return render_indicator_window(df, indicator, curr_date, look_back_days)


def get_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    *,
    information_frontier: str | None = None,
) -> str:
    """Return a J-Quants-backed deterministic market snapshot."""
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(days=look_back_days + _WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    df = _fetch_ohlcv_frame(symbol, start, curr_date)
    adjustment = str(df.attrs.get("price_adjustment") or "unknown")
    source = (
        "J-Quants adjusted OHLCV"
        if adjustment == "J-Quants adjusted OHLCV v2"
        else "J-Quants mixed adjusted/raw OHLCV"
    )
    body = render_verified_market_snapshot(
        df,
        symbol,
        curr_date,
        look_back_days,
        source=source,
        adjustment=adjustment,
    )
    latest = df.iloc[-1]
    latest_date = latest["Date"].strftime("%Y-%m-%d")
    latest_close = float(latest["Close"])
    digest_payload = [
        {
            "date": row["Date"].strftime("%Y-%m-%d"),
            **{
                column.casefold(): float(row[column])
                for column in ("Open", "High", "Low", "Close", "Volume")
            },
        }
        for _, row in df.iterrows()
    ]
    version_id = (
        "jquants-market:"
        + hashlib.sha256(json.dumps(digest_payload, separators=(",", ":")).encode()).hexdigest()[
            :20
        ]
    )
    conservative_available_at = datetime.fromisoformat(f"{latest_date}T23:59:59").replace(
        tzinfo=_TOKYO
    )
    availability_basis = "conservative market-date end; source has no intraday availability"
    if information_frontier is not None:
        collected_at = datetime.fromisoformat(information_frontier)
        if collected_at.utcoffset() is None:
            raise ValueError("J-Quants Information Frontier requires a timezone")
        collected_at = collected_at.astimezone(_TOKYO)
        if collected_at.date().isoformat() == latest_date:
            conservative_available_at = collected_at
            availability_basis = (
                "observed in successful bounded collection at Information Frontier"
            )
    observation = SourceObservation(
        source=source,
        record_id=f"jquants-market:{symbol.upper()}",
        version_id=version_id,
        status="published",
        published_at=latest_date,
        available_at=conservative_available_at.isoformat(),
        availability_basis=availability_basis,
        title=f"Adjusted market history through {latest_date}",
        record_kind="market",
        adjustment=adjustment,
        observation_value=latest_close,
        unit="JPY",
        precision=2,
    )
    first_date = df.iloc[0]["Date"]
    warmup_limitations = []
    if len(df) < _MIN_WARMUP_ROWS:
        warmup_limitations.append(
            f"Only {len(df)} rows were available; 200-row indicator warm-up is incomplete."
        )
    if latest["Date"] - first_date < timedelta(days=_MIN_WARMUP_SPAN_DAYS):
        warmup_limitations.append(
            "Returned history spans fewer than 280 calendar days; warm-up may be truncated."
        )
    if adjustment != "J-Quants adjusted OHLCV v2":
        warmup_limitations.append(
            "One or more adjusted OHLCV fields were absent and used raw fallback values."
        )
    limitations = tuple(warmup_limitations)
    watermark = SourceWatermark(
        source=source,
        scanned_start=first_date.strftime("%Y-%m-%d"),
        scanned_end=curr_date,
        status="complete" if not limitations else "limited",
        limitations=limitations,
        returned_records=len(df),
        requested_interval=SourceInterval(start=start, end=curr_date),
        limitation_kind="partial" if limitations else None,
        information_frontier=information_frontier,
    )
    return attach_source_watermarks(attach_source_observations(body, observation), watermark)
