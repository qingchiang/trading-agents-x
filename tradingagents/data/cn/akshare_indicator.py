"""A-share indicators and verified snapshot over shared AkShare qfq OHLCV."""

from __future__ import annotations

from datetime import datetime

from dateutil.relativedelta import relativedelta

from tradingagents.data.cn.akshare_stock import ADJUSTMENT_FALLBACK_NOTE, fetch_ohlcv
from tradingagents.data.context import DataRequestContext
from tradingagents.data.market_data_validator import render_verified_market_snapshot
from tradingagents.data.stockstats_utils import render_indicator_window
from tradingagents.domain.data import ProvenanceRecord
from tradingagents.provenance import attach_provenance

_WARMUP_DAYS = 450


def _technical_frame(symbol: str, curr_date: str):
    start = (
        datetime.strptime(curr_date, "%Y-%m-%d")
        - relativedelta(days=_WARMUP_DAYS)
    ).strftime("%Y-%m-%d")
    return fetch_ohlcv(symbol, start, curr_date)


def get_indicator(
    symbol: str, indicator: str, curr_date: str, look_back_days: int
, *, data_context: DataRequestContext) -> str:
    """Return a stockstats indicator window over validated qfq mainland bars."""
    result = _technical_frame(symbol, curr_date)
    metadata = (
        f"# Actual data source: {result.source}\n"
        f"# Price adjustment: {result.adjustment}\n"
        f"# Requested analysis date: {curr_date}\n"
        f"# Effective trading date: {result.effective_end}\n\n"
    )
    timing = "market-date filtered; qfq adjusted; future rows excluded"
    if result.fallback_reason:
        timing += (
            f"; fallback: {result.fallback_reason}; {ADJUSTMENT_FALLBACK_NOTE}"
        )
    return attach_provenance(
        metadata
        + render_indicator_window(
            result.frame, indicator, curr_date, look_back_days
        ),
        ProvenanceRecord(
            evidence=f"get_indicators/{indicator}",
            source=result.source,
            requested=curr_date,
            effective=result.effective_end,
            timing=timing,
        ),
    )


def get_verified_market_snapshot(
    symbol: str, curr_date: str, look_back_days: int = 30
, *, data_context: DataRequestContext) -> str:
    """Return a deterministic snapshot over the same cached qfq mainland bars."""
    result = _technical_frame(symbol, curr_date)
    return render_verified_market_snapshot(
        result.frame,
        result.canonical,
        curr_date,
        look_back_days,
        source=result.source,
        adjustment=result.adjustment,
        provenance_timing=(
            f"market-date filtered; rows after cutoff excluded; fallback: "
            f"{result.fallback_reason}; {ADJUSTMENT_FALLBACK_NOTE}"
            if result.fallback_reason
            else None
        ),
    )
