"""Shared analysis-date boundaries for live snapshots and trailing windows.

Some sources expose only their current state, not point-in-time history.  They
may enrich a live or near-live run, but must be omitted from older backtests.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .symbol_utils import market_today

# Today and this many preceding market-local calendar dates are near-live.
LIVE_SNAPSHOT_MAX_AGE_DAYS = 5


def is_near_live(
    curr_date: str,
    ticker: str | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a cutoff may safely use retrieval-time snapshots.

    The comparison uses the instrument's market calendar date. Only today and
    the preceding five dates qualify; future dates fail closed.
    """
    try:
        requested = date.fromisoformat(curr_date)
    except (TypeError, ValueError):
        return False
    age = (market_today(ticker, now) - requested).days
    return 0 <= age <= LIVE_SNAPSHOT_MAX_AGE_DAYS


def lookback_start_date(curr_date: str, lookback_days: int) -> str:
    """Return ``curr_date - lookback_days`` as an ISO date.

    Vendor date ranges are inclusive at both ends, matching the project's
    existing global-news convention.  Reject negative/bool values so a bad
    runtime override cannot silently create a future-facing request window.
    """
    if isinstance(lookback_days, bool) or not isinstance(lookback_days, int):
        raise ValueError(f"lookback_days must be an integer, got {lookback_days!r}")
    if lookback_days < 0:
        raise ValueError(f"lookback_days must be >= 0, got {lookback_days}")
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    return (end - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
