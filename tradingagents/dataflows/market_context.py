"""Suffix-based vendor routing.

A ticker's exchange suffix (e.g. ``.T`` for Tokyo, ``.SS``/``.SZ`` for China)
selects a market-specific vendor chain via the ``data_vendors_by_market`` config.
Only **ticker-bearing** methods are market-routed: they infer the market from
their first positional argument (the symbol).

**Ticker-less** methods (macro, global news, prediction markets) describe
cross-border context, not a single instrument, so they are deliberately
market-agnostic — they always use the default vendor chain. Macro in particular
is analyzed across all markets at once (US + Japan + China together), so there
is no per-run market state to track, and thus no thread/async propagation
hazard. This is also why, when ``news_data`` is routed for ``.T``, per-ticker
news goes to the JP vendor while ``get_global_news`` stays global — it falls out
of the ticker-less rule with no extra switch.

With ``data_vendors_by_market`` empty (the default), ``infer_market`` always
returns ``""`` and routing is byte-for-byte identical to before this module.
"""

from __future__ import annotations

from .config import get_config
from .symbol_utils import match_exchange_suffix

# Methods whose first positional argument is NOT a ticker. They are
# market-agnostic (cross-border context) and always use the default chain. Keep
# this in sync when adding ticker-less tools.
TICKERLESS_METHODS = frozenset(
    {"get_global_news", "get_macro_indicators", "get_prediction_markets"}
)


def market_suffix_of(symbol: str, routes: dict | None = None) -> str:
    """Return the configured market suffix the symbol belongs to, or "".

    Only suffixes present in ``data_vendors_by_market`` are considered (matched
    longest-first), so US tickers and dotted symbols like ``BRK.B`` stay on the
    default chain unless a route is configured for that suffix. ``routes`` may be
    passed to avoid re-reading the config on a hot path.
    """
    if routes is None:
        routes = get_config().get("data_vendors_by_market", {})
    return match_exchange_suffix(symbol, routes)


def infer_market(method: str, args: tuple, routes: dict | None = None) -> str:
    """Infer the market suffix for a vendor call.

    Ticker-bearing methods derive it from their first positional arg (the
    symbol); ticker-less methods are market-agnostic and return "" (default
    chain). ``routes`` is ``data_vendors_by_market``; pass it to reuse a config
    snapshot the caller already holds.
    """
    if method in TICKERLESS_METHODS or not args:
        return ""
    return market_suffix_of(str(args[0]), routes)
