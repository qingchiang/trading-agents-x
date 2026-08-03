"""Point-in-time-safe instrument identity resolution shared by dataflows.

Historical runs deliberately use Yahoo Search metadata only.  ``Ticker.info``
is a live snapshot and can otherwise leak a company's current classification
into an old analysis date.  Live runs retain the richer ``.info`` fields.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Iterable
from typing import Any

import yfinance as yf

from .lookahead import is_near_live
from .stockstats_utils import yf_retry
from .symbol_utils import normalize_symbol

logger = logging.getLogger(__name__)


def _clean_identity_value(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` for empty placeholder values."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.casefold() in {"none", "n/a", "nan", "null"}:
        return None
    return cleaned


def _identity_from_mapping(data: dict, *, live: bool) -> dict[str, str]:
    identity: dict[str, str] = {}
    long_name = _clean_identity_value(data.get("longName")) or _clean_identity_value(
        data.get("longname")
    )
    short_name = _clean_identity_value(data.get("shortName")) or _clean_identity_value(
        data.get("shortname")
    )
    company_name = long_name or short_name
    if company_name:
        identity["company_name"] = company_name
    if long_name:
        identity["long_name"] = long_name
    if short_name:
        identity["short_name"] = short_name

    fields = [("exchange", "exchange"), ("quoteType", "quote_type")]
    if live:
        fields[0:0] = [("sector", "sector"), ("industry", "industry")]
    for source_key, target_key in fields:
        value = _clean_identity_value(data.get(source_key))
        if value:
            identity[target_key] = value
    return identity


def _search_identity(canonical: str) -> dict[str, str]:
    """Resolve stable identity fields from an exact-symbol Yahoo Search hit."""
    search = yf_retry(
        lambda: yf.Search(
            query=canonical,
            max_results=8,
            news_count=0,
            enable_fuzzy_query=False,
        )
    )
    for quote in getattr(search, "quotes", None) or []:
        symbol = _clean_identity_value(quote.get("symbol"))
        if symbol and symbol.casefold() == canonical.casefold():
            return _identity_from_mapping(quote, live=False)
    return {}


def _live_identity(canonical: str) -> dict[str, str]:
    info = yf_retry(lambda: yf.Ticker(canonical).info) or {}
    return _identity_from_mapping(info, live=True)


@functools.lru_cache(maxsize=256)
def _resolve_cached(canonical: str, mode: str) -> dict[str, str]:
    try:
        if mode == "historical":
            return _search_identity(canonical)
        return _live_identity(canonical)
    except Exception as exc:  # noqa: BLE001 -- identity must never block a run
        logger.debug(
            "Could not resolve %s instrument identity for %s: %s",
            mode,
            canonical,
            exc,
        )
        return {}


def resolve_instrument_identity(ticker: str, curr_date: str | None = None) -> dict[str, str]:
    """Return identity metadata without using live ``.info`` in a backtest.

    Cache keys intentionally collapse all dates into ``live`` or ``historical``
    mode.  Identity is best-effort: an exact Search miss returns an empty dict,
    and historical mode never calls ``.info`` to fill missing fields.
    """
    canonical = normalize_symbol(ticker)
    mode = (
        "live"
        if curr_date is None or is_near_live(curr_date, canonical)
        else "historical"
    )
    return _resolve_cached(canonical, mode)


def resolve_search_identity(ticker: str) -> dict[str, str]:
    """Return exact-symbol Search identity for relevance filtering.

    This intentionally shares the resolver's ``historical`` cache entry: a
    historical graph startup and its ticker-news fetch therefore make one
    Search request, while live news still avoids treating ``.info`` fields as
    evidence that Yahoo attached an article to the requested symbol.
    """
    return _resolve_cached(normalize_symbol(ticker), "historical")


def clear_instrument_identity_cache() -> None:
    """Clear the shared resolver cache (primarily for tests and long-lived apps)."""
    _resolve_cached.cache_clear()


# Preserve the established ``resolve_instrument_identity.cache_clear()`` API.
resolve_instrument_identity.cache_clear = clear_instrument_identity_cache  # type: ignore[attr-defined]


def identity_names(identity: dict[str, str] | None) -> Iterable[str]:
    """Yield non-empty name fields accepted from resolver/vendor mappings."""
    if not identity:
        return
    for key in ("company_name", "long_name", "short_name", "name"):
        value = _clean_identity_value(identity.get(key))
        if value:
            yield value
