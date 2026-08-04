"""Ticker → Japanese company name for Tokyo issues, via J-Quants /equities/master.

The Google-News media feed searches by company *name*, not by securities code (a
bare code is noisy — ``4568`` also matches galaxy NGC 4568, index levels, etc.),
so we resolve the clean short name here. ``/equities/master`` returns ``CoName``
already in short form (``4568`` → ``第一三共``, no ``株式会社`` suffix to strip),
plus sector/market fields a future short-ratio mapping can reuse.

Names are stable, so lookups are memoized per code in a module-level cache. On any
J-Quants failure this degrades to ``None`` (the caller falls back to the bare
code) rather than raising — resolving a name must never abort a news fetch.
"""

from __future__ import annotations

import logging

from .jquants_common import memoized_fetch, to_jquants_code

logger = logging.getLogger(__name__)

# (code, as_of_date) -> list[master record]. An as-of key prevents today's
# Prime/Standard/Growth classification from leaking into a historical run.
_MASTER_CACHE: dict = {}

_MARKET_SECTIONS = {
    "0111": "TSEPrime",
    "0112": "TSEStandard",
    "0113": "TSEGrowth",
}


def _master_records(code: str, as_of_date: str | None = None) -> list[dict]:
    params = {"code": code}
    if as_of_date:
        params["date"] = as_of_date
    return memoized_fetch(
        _MASTER_CACHE,
        (code, as_of_date),
        "/equities/master",
        params,
        "data",
    )


def get_company_name(ticker: str, curr_date: str | None = None) -> str | None:
    """Return the Japanese short company name for a Tokyo ticker, or None.

    ``9984.T`` → ``ソフトバンクグループ``. Returns None when J-Quants has no master
    row for the code or the lookup fails (network/auth) — never raises.
    """
    code = to_jquants_code(ticker)
    try:
        records = _master_records(code, curr_date)
    except Exception as exc:  # never let name resolution abort a news fetch
        logger.warning("Company-name lookup failed for %s: %s", ticker, exc)
        return None
    if not records:
        return None
    # Master can carry multiple dated rows (e.g. after a rename); pick the latest
    # by Date — row order isn't guaranteed — so we resolve the current name.
    latest = max(records, key=lambda r: r.get("Date") or "")
    return latest.get("CoName") or None


def get_company_market_section(ticker: str, curr_date: str) -> str | None:
    """Return the ticker's J-Quants exchange section as of ``curr_date``.

    The investor-type endpoint accepts ``TSEPrime``, ``TSEStandard`` or
    ``TSEGrowth``. Unknown/unsupported classifications and lookup failures return
    ``None``; callers must never silently substitute Prime.
    """
    code = to_jquants_code(ticker)
    try:
        records = _master_records(code, curr_date)
    except Exception as exc:
        logger.warning("Company-market lookup failed for %s as of %s: %s", ticker, curr_date, exc)
        return None
    if not records:
        return None
    latest = max(records, key=lambda r: r.get("Date") or "")
    return _MARKET_SECTIONS.get(str(latest.get("Mkt") or ""))
