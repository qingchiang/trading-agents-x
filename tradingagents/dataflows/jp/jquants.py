"""Aggregated J-Quants (Japanese market) vendor entry points.

Single import surface for the routing layer, mirroring ``alpha_vantage.py``.
"""

from .jquants_fundamentals import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
)
from .jquants_indicator import get_indicator, get_verified_market_snapshot
from .jquants_stock import get_stock

__all__ = [
    "get_balance_sheet",
    "get_cashflow",
    "get_fundamentals",
    "get_income_statement",
    "get_indicator",
    "get_verified_market_snapshot",
    "get_stock",
]
