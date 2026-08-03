"""FRED (Federal Reserve Economic Data) macro vendor.

Fetches macroeconomic time series — policy rates, Treasury yields, inflation,
labor, growth — from the St. Louis Fed's free API. Used by the news analyst to
ground macro commentary in actual numbers rather than headlines alone.

A free API key (https://fred.stlouisfed.org/docs/api/api_key.html) is read from
``FRED_API_KEY``; if it is unset the vendor raises ``FredNotConfiguredError`` so
the routing layer treats it as "unavailable" rather than a hard crash.
"""
import logging
import os
import re
from datetime import datetime, timedelta

import requests

from .errors import VendorNotConfiguredError
from .macro_common import SeriesCache, render_macro_report

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred"
RAW_SERIES_ID = re.compile(r"^[A-Za-z0-9]{1,25}$")

# Network timeout (seconds) so a stalled request can't hang the agents,
# mirroring the Alpha Vantage client.
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for most monthly/quarterly series.
DEFAULT_LOOKBACK_DAYS = 365

# Curated human-friendly aliases -> FRED series IDs. Anything not listed is used
# verbatim as a raw FRED series ID, so power users are never limited to this set.
MACRO_SERIES = {
    # Policy rate & Treasury yields
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "fed_funds": "FEDFUNDS",
    "2y_treasury": "DGS2",
    "10y_treasury": "DGS10",
    "30y_treasury": "DGS30",
    "10y_2y_spread": "T10Y2Y",
    "yield_curve": "T10Y2Y",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "inflation_expectations": "T10YIE",
    # Growth & output
    "real_gdp": "GDPC1",
    "gdp": "GDP",
    "industrial_production": "INDPRO",
    # Labor
    "unemployment_rate": "UNRATE",
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    # Money & markets
    "m2": "M2SL",
    "money_supply": "M2SL",
    "vix": "VIXCLS",
    "dollar_index": "DTWEXBGS",
    # Sentiment & housing
    "consumer_sentiment": "UMCSENT",
    "housing_starts": "HOUST",
    "retail_sales": "RSAFS",
}


class FredNotConfiguredError(VendorNotConfiguredError):
    """Raised when FRED is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


def get_api_key() -> str:
    """Retrieve the FRED API key from the environment."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise FredNotConfiguredError(
            "FRED_API_KEY environment variable is not set. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html."
        )
    return api_key


def _resolve_series_id(indicator: str) -> str:
    """Map a friendly alias to a FRED series ID, or pass a raw ID through.

    Raises ``ValueError`` when the input is neither a known alias nor a plausible
    series ID — typically a descriptive phrase the LLM passed instead (e.g.
    "bank of japan rate"). FRED IDs are short and alphanumeric, so this rejects
    it up front with guidance rather than letting it 400 the API.
    """
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    candidate = indicator.strip().upper()
    if not RAW_SERIES_ID.fullmatch(candidate):
        raise ValueError(
            "Indicator is not a known macro alias or a valid FRED series ID. "
            "Use an alias (e.g. 'cpi', 'unemployment', '10y_treasury') or a raw "
            "1-25 character alphanumeric FRED series ID (e.g. 'CPIAUCSL')."
        )
    return candidate


def _request(path: str, params: dict) -> dict:
    """GET a FRED endpoint, surfacing FRED's JSON error body on a bad request."""
    api_params = {**params, "api_key": get_api_key(), "file_type": "json"}
    response = requests.get(
        f"{FRED_API_BASE}/{path}", params=api_params, timeout=REQUEST_TIMEOUT
    )
    # FRED returns 400 with a JSON {"error_message": ...} for unknown series IDs
    # or malformed params; turn that into a clear, actionable error.
    if response.status_code == 400:
        try:
            message = response.json().get("error_message", response.text)
        except ValueError:
            message = response.text
        raise ValueError(f"FRED request failed: {message}")
    response.raise_for_status()
    return response.json()


# Process-level cache of fetched series, keyed by (series_id, curr_date,
# look_back_days). A macro series is a point-in-time function of curr_date, so a
# panel that pulls ten series plus a later microscope tool-call for the same
# series within one run hits the API once — and the news node is re-entered on
# every tool-call round-trip, so caching (including curr_date == today, which for
# low-frequency macro is effectively settled) is what keeps a run from hammering
# FRED's rate limit. Only *successful* results are cached (see SeriesCache). The
# "fred" namespace also persists settled (past-date) series to disk, so a backtest
# re-reading the same dates across runs skips the API entirely.
_series_cache = SeriesCache(namespace="fred")


def fetch_series(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> dict | None:
    """Fetch a FRED series as structured data (metadata + observations).

    Returns a dict with ``series_id``, ``title``, ``units``, ``frequency``,
    ``seasonal``, ``start_date`` and ``points`` (a look-ahead-safe ascending list
    of ``(date, value)`` up to ``curr_date``), or ``None`` if the series does not
    exist. Raises ``ValueError`` for an unusable indicator and
    ``FredNotConfiguredError`` when no key is set. Shared by :func:`get_macro_data`
    (the microscope tool) and the cross-region macro panel, memoized per
    (series, date, window).
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS
    series_id = _resolve_series_id(indicator)

    cache_key = (series_id, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=look_back_days)).strftime("%Y-%m-%d")

    meta = _request("series", {"series_id": series_id}).get("seriess") or []
    if not meta:
        # Don't cache a miss: an empty response may be a transient outage rather
        # than a genuinely nonexistent series, and we want a later call to retry.
        return None
    info = meta[0]

    observations = _request(
        "series/observations",
        {
            "series_id": series_id,
            "observation_start": start_date,
            "observation_end": curr_date,
            "sort_order": "asc",
        },
    ).get("observations", [])

    # FRED encodes a missing observation as ".".
    points = [
        (o["date"], o["value"])
        for o in observations
        if o.get("value") not in (".", None, "")
    ]

    data = {
        "series_id": series_id,
        "title": info.get("title", series_id),
        "units": info.get("units_short") or info.get("units", ""),
        "frequency": info.get("frequency", ""),
        "seasonal": info.get("seasonal_adjustment_short", ""),
        "start_date": start_date,
        "points": points,
    }
    _series_cache.put(cache_key, data)
    return data


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Fetch a FRED macroeconomic series as a formatted markdown report.

    Args:
        indicator: A friendly alias (e.g. "cpi", "unemployment", "10y_treasury")
            or a raw FRED series ID (e.g. "CPIAUCSL", "DGS10").
        curr_date: End of the window (yyyy-mm-dd); no later observations are
            returned, so a past date never leaks future data.
        look_back_days: Trailing window length; ``None`` uses DEFAULT_LOOKBACK_DAYS.

    Returns:
        A markdown report with the series title, units, frequency, the latest
        value, the change over the window, and a recent observation table.
    """
    # Invalid LLM-supplied indicator: return guidance rather than raising, so a
    # bad argument doesn't abort the run (the routing layer also degrades macro
    # data, but a specific message is more useful to the analyst). A missing key
    # (FredNotConfiguredError, also a ValueError) must still propagate so macro
    # degrades at the router rather than reading as a bad indicator.
    try:
        data = fetch_series(indicator, curr_date, look_back_days)
    except FredNotConfiguredError:
        raise
    except ValueError as e:
        return f"FRED: {e}"

    if data is None:
        series_id = _resolve_series_id(indicator)
        return (
            f"FRED series '{series_id}' not found. Pass a known alias "
            f"(e.g. 'cpi', 'unemployment') or a valid FRED series ID."
        )

    return render_macro_report("FRED", data, curr_date)
