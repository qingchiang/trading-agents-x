"""Bank of Japan macro vendor — BOJ Time-Series Data Search API.

The BOJ launched a public REST API (Feb 2026) for its time-series statistics; it
needs **no key or registration** (https://www.stat-search.boj.or.jp/). We use it
for two indicators FRED does not serve well: the daily uncollateralized overnight
call rate (the policy-rate anchor, fresher than FRED's monthly OECD mirror) and
the quarterly Tankan business-conditions DI (BOJ-exclusive, the headline forward
activity gauge for Japan).

Backs the Japan policy-rate and Tankan cells of the cross-region macro panel
(:mod:`macro_panel`) via :func:`fetch_series`, whose return shape mirrors
:func:`fred.fetch_series` so the panel renders every source through one path.

Look-ahead safety: BOJ returns the full history regardless of the query date, so
a past ``curr_date`` would otherwise see values that were not yet published. Each
observation is therefore kept only if its *availability* date (≈ publication
date) is ``<= curr_date``: for the daily rate that is the observation date; for
the quarterly Tankan it is the first day of the month after the reference quarter
(Tankan is surveyed in the quarter's last month and published early the next).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import NamedTuple

import requests

from .errors import NoMarketDataError
from .macro_common import SeriesCache, render_macro_report

logger = logging.getLogger(__name__)

BOJ_API_BASE = "https://www.stat-search.boj.or.jp/api/v1"

# Network timeout (seconds) so a stalled request can't hang the agents.
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for both the daily and quarterly series.
DEFAULT_LOOKBACK_DAYS = 365


class _Series(NamedTuple):
    """A BOJ database + series code with its frequency and a human title."""
    db: str
    code: str
    freq: str   # "D" (daily) or "Q" (quarterly)
    title: str


# Curated aliases -> BOJ (db, series code, frequency, title). Codes verified
# against getMetadata (2026-06): FM01/STRDCLUCON = uncollateralized overnight call
# rate (avg, daily, % p.a.); CO/TK99F1000601GCQ01000 = Tankan D.I. business
# conditions, Large Enterprises / Manufacturing / Actual result (quarterly, DI pts).
BOJ_SERIES = {
    "jp_policy_rate": _Series("FM01", "STRDCLUCON", "D",
                              "Japan policy rate (overnight call, avg)"),
    "jp_tankan":      _Series("CO", "TK99F1000601GCQ01000", "Q",
                              "Japan Tankan (large mfg business conditions DI)"),
}


def _request(path: str, params: dict) -> dict:
    """GET a BOJ API endpoint and unwrap its envelope, mapping STATUS to errors.

    The API is keyless. ``STATUS`` is 200 on success (message id ``M181030I`` =
    "no applicable data", still 200); 400/500/503 indicate parameter or server
    errors and raise ``ValueError`` with the API's own message.
    """
    resp = requests.get(f"{BOJ_API_BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    status = body.get("STATUS")
    if status != 200:
        raise ValueError(
            f"BOJ request failed (STATUS {status}): {body.get('MESSAGE', '')}"
        )
    return body


def _quarter(month: int) -> int:
    """Calendar quarter (1-4) containing ``month``."""
    return (month - 1) // 3 + 1


def _request_dates(freq: str, start_dt: datetime, end_dt: datetime) -> tuple[str, str]:
    """Format the window bounds for the API per frequency (daily=YYYYMM, quarterly=YYYYQQ)."""
    if freq == "Q":
        start = f"{start_dt.year}{_quarter(start_dt.month):02d}"
        end = f"{end_dt.year}{_quarter(end_dt.month):02d}"
        return start, end
    # Daily (and weekly/monthly) bounds are specified in YYYYMM.
    return start_dt.strftime("%Y%m"), end_dt.strftime("%Y%m")


def _parse_point(freq: str, survey_date: int) -> tuple[str, str]:
    """Map a BOJ survey-date code to ``(display_date, availability_date)``.

    ``display_date`` labels the observation (ISO for daily, "YYYY Qn" for
    quarterly); ``availability_date`` is the ISO date the value became public,
    used for the look-ahead filter (see the module docstring).
    """
    if freq == "Q":
        year, q = divmod(survey_date, 100)
        # Published in the month after the reference quarter's last month (q*3),
        # rolling Q4 (Dec) over to January of the next year.
        avail_year, avail_month = (year, q * 3 + 1) if q < 4 else (year + 1, 1)
        return f"{year} Q{q}", f"{avail_year:04d}-{avail_month:02d}-01"
    year, md = divmod(survey_date, 10000)
    month, day = divmod(md, 100)
    iso = f"{year:04d}-{month:02d}-{day:02d}"
    return iso, iso


# Process-level cache, keyed by (alias, curr_date, look_back_days). Mirrors fred:
# one HTTP call per series for the life of the process. Only successful results
# are cached (see SeriesCache); a miss is not memoized so a transient outage can't
# poison a series. The "boj" namespace also persists settled (past-date) series to
# disk for reuse across runs (e.g. a multi-date backtest).
_series_cache = SeriesCache(namespace="boj")


def fetch_series(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> dict | None:
    """Fetch a BOJ series as structured data, mirroring :func:`fred.fetch_series`.

    Returns a dict with ``series_id``, ``title``, ``units``, ``frequency``,
    ``seasonal``, ``start_date`` and ``points`` (a look-ahead-safe ascending list
    of ``(date, value)`` whose availability date is ``<= curr_date``), or ``None``
    when the window holds no usable observations. Raises ``ValueError`` for an
    unknown alias or a BOJ API error.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS
    key = indicator.strip().lower()
    if key not in BOJ_SERIES:
        raise ValueError(
            f"'{indicator}' is not a known BOJ macro alias. "
            f"Known aliases: {', '.join(sorted(BOJ_SERIES))}."
        )
    spec = BOJ_SERIES[key]

    cache_key = (key, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days)
    start_param, end_param = _request_dates(spec.freq, start_dt, end_dt)

    body = _request(
        "getDataCode",
        {
            "format": "json", "lang": "en", "db": spec.db, "code": spec.code,
            "startDate": start_param, "endDate": end_param,
        },
    )
    results = body.get("RESULTSET") or []
    if not results:
        return None
    series = results[0]
    values = series.get("VALUES") or {}
    survey_dates = values.get("SURVEY_DATES") or []
    raw_values = values.get("VALUES") or []

    # Build ascending (display_date, value); skip nulls and, by availability date,
    # anything not yet published as of curr_date (look-ahead). The display date
    # sorts chronologically (ISO for daily, "YYYY Qn" for quarterly), so it is
    # also the sort key — no need to carry the availability date past the filter.
    points = []
    for survey_date, raw in zip(survey_dates, raw_values, strict=False):
        if raw is None:
            continue
        display, avail = _parse_point(spec.freq, int(survey_date))
        if avail <= curr_date:
            points.append((display, str(raw)))
    points.sort()

    if not points:
        # No usable observations (transient outage, all-null window, or — for a
        # backtest at an early date — nothing published yet). Not cached: a later
        # call at the same date may legitimately retry once data exists/recovers.
        return None

    data = {
        "series_id": spec.code,
        "title": spec.title,
        "units": series.get("UNIT", ""),
        "frequency": series.get("FREQUENCY", ""),
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
    """Render a BOJ series as a markdown report (the microscope path).

    Normally reached only for an owned alias (the macro dispatcher routes by
    indicator); it renders via the shared formatter, or returns a "no data" note
    when the window is empty. Raises ``NoMarketDataError`` if called directly with
    an indicator the BOJ vendor doesn't serve.
    """
    if indicator.strip().lower() not in BOJ_SERIES:
        raise NoMarketDataError(indicator, detail="not a BOJ series")
    data = fetch_series(indicator, curr_date, look_back_days)
    if data is None:
        return f"BOJ: no data for '{indicator}' in this window."
    return render_macro_report("BOJ", data, curr_date)
