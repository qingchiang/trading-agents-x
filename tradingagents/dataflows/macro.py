"""Macro indicator dispatch: route an indicator to the vendor that owns it.

The macro microscope tool (``get_macro_indicators``) accepts a free-form indicator
and must reach whichever source serves it: US series (and raw FRED IDs) go to
:mod:`fred`, Japan's CPI to :mod:`estat`, Japan's policy rate / Tankan to
:mod:`boj`. This is **content dispatch by indicator**, not the router's
fallback-chain — each Japanese alias is owned by exactly one vendor, and the
owning vendor's typed errors (``VendorNotConfiguredError`` / ``NoMarketDataError``)
must propagate so the router degrades with the *right* reason (e.g. "FRED_API_KEY
missing" for a US series, "ESTAT_APP_ID missing" for ``jp_cpi``). A fallback chain
would instead surface the first vendor's rejection, naming the wrong source.

Registered as the default ``macro_data`` vendor; the panel resolves the same
owners per cell, so panel and microscope agree on any indicator.
"""
from . import boj, estat, fred


def get_macro_indicators(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Dispatch ``indicator`` to its owning macro vendor and return its report."""
    key = indicator.strip().lower()
    if key in estat.ESTAT_SERIES:
        return estat.get_macro_data(indicator, curr_date, look_back_days)
    if key in boj.BOJ_SERIES:
        return boj.get_macro_data(indicator, curr_date, look_back_days)
    return fred.get_macro_data(indicator, curr_date, look_back_days)
