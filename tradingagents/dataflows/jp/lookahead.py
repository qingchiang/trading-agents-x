"""Shared look-ahead gate for live-only JP overlays.

Some JP signals come from yfinance ``.info`` — a LIVE snapshot with no as-of
history (analyst-consensus forward, analyst ratings). Showing today's snapshot
on a historical ``curr_date`` would leak the future into a backtest, so those
overlays are emitted ONLY when ``curr_date`` is within a few days of today (a
live / near-live run). A backtest date is always far from today, so the overlay
stays absent there — the run simply has less data, never wrong (future) data,
which matches the fork's live-first, don't-leak stance.
"""

from __future__ import annotations

from datetime import datetime

# Within this many days of today (either side) counts as a live/near-live run.
LIVE_SNAPSHOT_MAX_AGE_DAYS = 5


def is_live(curr_date: str) -> bool:
    """True when ``curr_date`` is within ``LIVE_SNAPSHOT_MAX_AGE_DAYS`` of today.

    Uses the wall clock deliberately: live overlays are not meant to be
    reproducible, while a backtest date is always far from today, so backtests
    stay deterministic and look-ahead safe. A malformed date is treated as
    not-live.
    """
    try:
        age = (datetime.now() - datetime.strptime(curr_date, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return False
    # abs(): within N days EITHER side of today counts as live — a small negative
    # age (curr_date resolved in JST while the host clock lags) is still live,
    # while a far-future date is correctly rejected.
    return abs(age) <= LIVE_SNAPSHOT_MAX_AGE_DAYS
