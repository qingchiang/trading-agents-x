"""Compatibility re-export for the shared live-snapshot look-ahead gate.

The canonical implementation moved out of ``jp`` because US social feeds and
yfinance ``.info`` use the same policy. Keep this module so existing internal
imports and downstream users do not break.
"""

from ..lookahead import LIVE_SNAPSHOT_MAX_AGE_DAYS, is_near_live

__all__ = ["LIVE_SNAPSHOT_MAX_AGE_DAYS", "is_near_live"]
