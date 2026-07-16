"""Shared look-ahead gate for live-only vendor snapshots.

Some sources expose only their current state, not point-in-time history.  They
may enrich a live or near-live run, but must be omitted from older backtests.
"""

from __future__ import annotations

from datetime import datetime

# Within this many days of today (either side) counts as live/near-live.
LIVE_SNAPSHOT_MAX_AGE_DAYS = 5


def is_live(curr_date: str) -> bool:
    """Return whether ``curr_date`` is close enough to today for live snapshots."""
    try:
        age = (datetime.now() - datetime.strptime(curr_date, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return False
    return abs(age) <= LIVE_SNAPSHOT_MAX_AGE_DAYS
