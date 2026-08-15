"""Deterministic scheduling helpers for five-interval outcome settlement."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from tradingagents.dataflows.symbol_utils import market_timezone


def earliest_outcome_check_at(
    *,
    ticker: str,
    analysis_date: date,
    holding_intervals: int,
) -> datetime:
    """Return the earliest plausible UTC check after all required closes.

    Supported markets use a weekday lower bound; exchange holidays are
    deliberately left to provider observations and the persisted retry
    schedule.
    """
    required_closes = holding_intervals + 1
    candidate = analysis_date
    observed = 0
    while observed < required_closes:
        if candidate.weekday() < 5:
            observed += 1
        candidate += timedelta(days=1)
    local_midnight = datetime.combine(
        candidate,
        time.min,
        tzinfo=market_timezone(ticker),
    )
    return local_midnight.astimezone(UTC)
