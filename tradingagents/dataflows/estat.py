"""e-Stat (政府統計の総合窓口) macro vendor — Japanese official statistics.

e-Stat is the Japanese government's statistics portal. Its v3 REST API serves
time series (CPI, unemployment, GDP, ...) keyed by a table id (``statsDataId``)
plus classification codes. We use it for indicators FRED does not mirror in a
timely way — notably Japan's CPI, whose FRED OECD mirror was discontinued ~2021.

A free application id (https://www.e-stat.go.jp/api/) is read from
``ESTAT_APP_ID``; if it is unset (or rejected) the vendor raises
``EstatNotConfiguredError`` so the caller treats it as "unavailable" rather than
crashing.

Today this backs the Japan inflation cells of the cross-region macro panel
(:mod:`macro_panel`) via :func:`fetch_series`, whose return shape mirrors
:func:`fred.fetch_series` so the panel renders both sources through one code path.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import requests

from .errors import NoMarketDataError, VendorNotConfiguredError
from .macro_common import SeriesCache, render_macro_report

logger = logging.getLogger(__name__)

ESTAT_API_BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"

# Network timeout (seconds) so a stalled request can't hang the agents, matching
# the other vendors.
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for these monthly series.
DEFAULT_LOOKBACK_DAYS = 365


# e-Stat CPI table 0003427113 (2020-base, nationwide monthly). Every alias names
# an item (cat01) within this one table; the area axis is fixed to 全国 (00000) and
# the 表章 axis to the index value (tab=1), so summarize_points' change over a ~1y
# window ≈ YoY — exactly how the FRED-sourced US CPI cell is rendered. Values are
# index points (2020=100). Codes verified against getMetaInfo (2026-06).
_CPI_STATS_DATA_ID = "0003427113"
_CPI_AREA_NATIONWIDE = "00000"
_CPI_TAB_INDEX = "1"

# Curated alias -> (cat01 item code, human title).
ESTAT_SERIES = {
    "jp_cpi":           ("0001", "Japan CPI (all items)"),
    "jp_core_cpi":      ("0161", "Japan CPI (ex-fresh food)"),
    "jp_core_core_cpi": ("0178", "Japan CPI (ex-food & energy)"),
}

# e-Stat RESULT.STATUS: 0 = ok, 1 = ok-but-no-data, 100 = appId auth failure.
_STATUS_OK = (0, 1)
_STATUS_AUTH_FAILURE = 100


class EstatNotConfiguredError(VendorNotConfiguredError):
    """Raised when e-Stat is selected but ``ESTAT_APP_ID`` is unset or rejected.

    A VendorNotConfiguredError (and thus a ValueError), so the routing layer's
    "vendor unavailable" handling treats it the same as the other macro vendors.
    """


def get_app_id() -> str:
    """Return the e-Stat application id from the environment."""
    app_id = os.getenv("ESTAT_APP_ID")
    if not app_id:
        raise EstatNotConfiguredError(
            "ESTAT_APP_ID environment variable is not set. Get a free application "
            "id at https://www.e-stat.go.jp/api/."
        )
    return app_id


def _request(path: str, params: dict) -> dict:
    """GET an e-Stat endpoint and unwrap its envelope, mapping STATUS to errors.

    e-Stat returns HTTP 200 even for errors, signalling the outcome in
    ``RESULT.STATUS`` (0 = ok, 1 = ok-but-no-data, 100 = appId auth failure). An
    auth failure raises ``EstatNotConfiguredError``; any other non-ok status
    raises ``ValueError`` with the API's own message. Returns the endpoint's
    response object (the single top-level value, e.g. ``GET_STATS_DATA``).
    """
    resp = requests.get(
        f"{ESTAT_API_BASE}/{path}",
        params={**params, "appId": get_app_id()},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    # The single top-level key mirrors the endpoint (getStatsData -> GET_STATS_DATA).
    root = next(iter(resp.json().values()))
    result = root.get("RESULT", {})
    status = result.get("STATUS")
    if status == _STATUS_AUTH_FAILURE:
        raise EstatNotConfiguredError(
            f"e-Stat rejected ESTAT_APP_ID: {result.get('ERROR_MSG', '')}"
        )
    if status not in _STATUS_OK:
        raise ValueError(
            f"e-Stat request failed (STATUS {status}): {result.get('ERROR_MSG', '')}"
        )
    return root


def _month_code(year: int, month: int) -> str:
    """Build e-Stat's monthly time code, e.g. (2026, 6) -> '2026000606'."""
    return f"{year}00{month:02d}{month:02d}"


def _date_from_time_code(code: str) -> str:
    """Parse an e-Stat monthly time code to an ISO date, e.g. '2026000505' -> '2026-05-01'."""
    return f"{code[:4]}-{code[6:8]}-01"


# Process-level cache, keyed by (alias, curr_date, look_back_days). Mirrors fred:
# a macro series is a point-in-time function of curr_date, so the panel and a
# same-run re-entry hit the API once. Only successful results are cached (see
# SeriesCache); a miss is not memoized so a transient outage can't poison a series.
# The "estat" namespace also persists settled (past-date) series to disk for reuse
# across runs (e.g. a multi-date backtest).
_series_cache = SeriesCache(namespace="estat")


def fetch_series(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> dict | None:
    """Fetch a Japan CPI series as structured data, mirroring :func:`fred.fetch_series`.

    Returns a dict with ``series_id``, ``title``, ``units``, ``frequency``,
    ``seasonal``, ``start_date`` and ``points`` (a look-ahead-safe ascending list
    of ``(date, value)`` whose reference month is ``<= curr_date``), or ``None``
    when the window holds no usable observations. Raises ``ValueError`` for an
    unknown alias and ``EstatNotConfiguredError`` when no app id is set.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS
    key = indicator.strip().lower()
    if key not in ESTAT_SERIES:
        raise ValueError(
            f"'{indicator}' is not a known e-Stat macro alias. "
            f"Known aliases: {', '.join(sorted(ESTAT_SERIES))}."
        )
    cat01, title = ESTAT_SERIES[key]

    cache_key = (key, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days)

    root = _request(
        "getStatsData",
        {
            "statsDataId": _CPI_STATS_DATA_ID,
            "cdTab": _CPI_TAB_INDEX,
            "cdCat01": cat01,
            "cdArea": _CPI_AREA_NATIONWIDE,
            "cdTimeFrom": _month_code(start_dt.year, start_dt.month),
            "cdTimeTo": _month_code(end_dt.year, end_dt.month),
        },
    )
    values = root.get("STATISTICAL_DATA", {}).get("DATA_INF", {}).get("VALUE", [])
    if isinstance(values, dict):  # a single observation is returned unwrapped
        values = [values]

    # Build ascending (date, value); skip non-numeric markers ("-", "***", ...) and,
    # defensively, any reference month after curr_date (look-ahead — matching fred's
    # observation_end <= curr_date rule; cdTimeTo already truncates server-side).
    points = []
    for v in values:
        raw = v.get("$")
        try:
            float(raw)
        except (TypeError, ValueError):
            continue
        date = _date_from_time_code(v["@time"])
        if date <= curr_date:
            points.append((date, raw))
    points.sort()

    if not points:
        return None  # don't cache a miss: it may be a transient outage

    data = {
        "series_id": f"{_CPI_STATS_DATA_ID}/{cat01}",
        "title": title,
        "units": "2020=100",
        "frequency": "Monthly",
        "seasonal": "",
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "points": points,
    }
    _series_cache.put(cache_key, data)
    return data


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Render a Japan CPI series as a markdown report (the microscope path).

    Normally reached only for an owned alias (the macro dispatcher routes by
    indicator); it renders via the shared formatter, or returns a "no data" note
    when the window is empty. ``EstatNotConfiguredError`` propagates so the router
    degrades macro gracefully when ``ESTAT_APP_ID`` is unset. Raises
    ``NoMarketDataError`` if called directly with an indicator e-Stat doesn't serve.
    """
    if indicator.strip().lower() not in ESTAT_SERIES:
        raise NoMarketDataError(indicator, detail="not an e-Stat series")
    data = fetch_series(indicator, curr_date, look_back_days)
    if data is None:
        return f"e-Stat: no data for '{indicator}' in this window."
    return render_macro_report("e-Stat", data, curr_date)
