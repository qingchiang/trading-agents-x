"""Application eligibility for Japanese Research Chain market data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.jp.calendar import (
    completed_market_date,
    is_tse_open,
    tokyo_now,
)
from tradingagents.dataflows.jp.jquants_stock import fetch_latest_daily_bar_date


class MarketDataNotReadyError(RuntimeError):
    """The requested Research Chain cutoff is not yet eligible for execution."""


@dataclass(frozen=True)
class MarketDataReadiness:
    """Requested research cutoff and the distinct observed market session."""

    requested_cutoff: date
    market_effective_date: date
    observed_bar_date: date


def validate_jquants_daily_bar_ready(
    symbol: str,
    cutoff: date,
    *,
    now: datetime | None = None,
) -> MarketDataReadiness:
    """Require a completed expected TSE session and its actual J-Quants bar."""
    current = tokyo_now(now)
    if cutoff > current.date():
        raise MarketDataNotReadyError(
            f"J-Quants daily bar cutoff {cutoff} is in the future in Asia/Tokyo."
        )
    effective = completed_market_date(cutoff, current)
    if cutoff == current.date() and is_tse_open(cutoff) and effective != cutoff:
        raise MarketDataNotReadyError(
            f"J-Quants daily bar for {cutoff} is not eligible before the "
            "conservative 17:00 Asia/Tokyo readiness boundary."
        )
    observed = fetch_latest_daily_bar_date(
        symbol,
        effective.isoformat(),
        cutoff.isoformat(),
    )
    if observed != effective:
        raise NoMarketDataError(
            symbol,
            detail=(
                f"J-Quants daily bar for expected market date {effective} is not ready; "
                f"latest available daily bar is {observed}"
            ),
        )
    return MarketDataReadiness(
        requested_cutoff=cutoff,
        market_effective_date=effective,
        observed_bar_date=observed,
    )
