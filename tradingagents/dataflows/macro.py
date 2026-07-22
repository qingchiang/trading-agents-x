"""Macro indicator dispatch: route an indicator to the vendor that owns it.

The macro microscope tool (``get_macro_indicators``) accepts a free-form indicator
and must reach whichever source serves it: US series (and raw FRED IDs) go to
:mod:`fred`, Japan's CPI to :mod:`estat`, Japan's policy rate / Tankan to
:mod:`boj`, Japan's 10Y yield to :mod:`jp_macro`, and China aliases to
:mod:`cn_macro`. This is **content dispatch by
indicator**, not the router's fallback-chain — each regional alias is owned by
exactly one vendor, and the
owning vendor's typed errors (``VendorNotConfiguredError`` / ``NoMarketDataError``)
must propagate so the router degrades with the *right* reason (e.g. "FRED_API_KEY
missing" for a US series, "ESTAT_APP_ID missing" for ``jp_cpi``). A fallback chain
would instead surface the first vendor's rejection, naming the wrong source.

Registered as the default ``macro_data`` vendor; the panel resolves the same
owners per cell, so panel and microscope agree on any indicator.
"""

from tradingagents.provenance import ProvenanceRecord, attach_provenance

from . import boj, cn_macro, estat, fred, jp_macro


def _provenance_status(result: str, source: str, curr_date: str) -> tuple[str, str]:
    """Classify the dispatcher's deterministic success/empty/error text."""
    lowered = result.casefold()
    if ": no data for " in lowered or (
        source == "FRED" and lowered.startswith("fred series '") and " not found" in lowered
    ):
        return "—", "available; no observations in requested window"
    if source == "FRED" and lowered.startswith("fred: "):
        return "—", "invalid indicator or vendor request"
    return f"observations <= {curr_date}", "observation-date filtered"


def get_macro_indicators(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Dispatch ``indicator`` to its owning macro vendor and return its report."""
    key = indicator.strip().lower()
    source_timing = None
    if key in cn_macro.CN_SERIES:
        report = cn_macro.get_macro_report(indicator, curr_date, look_back_days)
        source, result, source_timing = report.source, report.text, report.timing
    elif key in jp_macro.JP_SERIES:
        report = jp_macro.get_macro_report(indicator, curr_date, look_back_days)
        source, result, source_timing = report.source, report.text, report.timing
    elif key in estat.ESTAT_SERIES:
        source, result = "e-Stat", estat.get_macro_data(indicator, curr_date, look_back_days)
    elif key in boj.BOJ_SERIES:
        source, result = "BOJ", boj.get_macro_data(indicator, curr_date, look_back_days)
    else:
        source, result = "FRED", fred.get_macro_data(indicator, curr_date, look_back_days)
    effective, timing = _provenance_status(result, source, curr_date)
    if source_timing is not None:
        timing = source_timing
    return attach_provenance(
        result,
        ProvenanceRecord(
            evidence="get_macro_indicators",
            source=source,
            requested=curr_date,
            effective=effective,
            timing=timing,
        ),
    )
