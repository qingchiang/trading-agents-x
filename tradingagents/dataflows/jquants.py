"""Aggregated J-Quants (Japanese market) vendor entry points.

Single import surface for the routing layer, mirroring ``alpha_vantage.py``.
"""

from .jquants_indicator import get_indicator
from .jquants_stock import get_stock

__all__ = ["get_indicator", "get_stock"]
