"""Shared market predicate for the JP data sources.

Several JP-only prefetch signals (News market-flow context, large-shareholding filings,
analyst-ratings overlay) self-gate to Tokyo names so a future market (``.SS`` …)
never inherits Japan's numbers. They gate on the ticker's *nationality* (its
``.T`` suffix), deliberately NOT on ``market_context.market_suffix_of`` — that is
config-driven (fires only when ``.T`` is wired into ``data_vendors_by_market``),
whereas these signals should hold for any Tokyo ticker regardless of routing.

One definition so the three call sites don't drift. When the China branch adds a
second market (N=2) this folds into a suffix→source registry.
"""

from __future__ import annotations


def is_tokyo_ticker(ticker: str) -> bool:
    """True for a Tokyo Stock Exchange ticker (``.T`` suffix), else False."""
    return str(ticker).upper().endswith(".T")
