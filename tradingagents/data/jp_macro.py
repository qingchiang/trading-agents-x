"""Japanese 10-year yield with an official MOF primary and FRED fallback."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import NamedTuple

from tradingagents.data import fred
from tradingagents.data.context import DataRequestContext
from tradingagents.data.jp import mof_yield
from tradingagents.data.jp.calendar import is_government_business_day
from tradingagents.data.macro_common import SeriesCache, render_macro_report
from tradingagents.domain.vendor_errors import NoMarketDataError

_FRED_10Y = "IRLTLT01JPM156N"

JP_SERIES = {"jp_10y_yield"}
_series_cache = SeriesCache(namespace="jp")


class MacroReport(NamedTuple):
    text: str
    source: str
    timing: str


def _cache_phase(requested_end, now: datetime) -> str:
    """Split today's cache at the one intraday MOF publication boundary."""
    if requested_end < now.date() or not is_government_business_day(now.date()):
        return "fixed"
    phase = "before-0930" if now.time() < time(9, 30) else "after-0930"
    return f"{now.date().isoformat()}-{phase}"


def _fetch_primary(start, end, *, as_of: datetime, now: datetime, data_context: DataRequestContext):
    return mof_yield.fetch_points(start, end, as_of=as_of, now=now, data_context=data_context)


def fetch_series(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
    *,
    data_context: DataRequestContext,
) -> dict | None:
    """Fetch JP10Y from MOF daily data, then use FRED monthly fallback."""
    key = indicator.strip().lower()
    if key not in JP_SERIES:
        raise ValueError(f"Unknown Japan macro alias: {indicator!r}")
    look_back_days = 365 if look_back_days is None else look_back_days
    requested_end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    now = mof_yield.tokyo_now()
    end = min(requested_end, now.date())
    as_of = mof_yield.analysis_as_of(end, now)
    cache_key = (key, curr_date, look_back_days, _cache_phase(requested_end, now))
    cached = _series_cache.get(cache_key, data_context=data_context)
    if cached is not None:
        return cached

    start = end - timedelta(days=look_back_days)
    fallback_reason = "MOF returned no usable observations"
    try:
        points = _fetch_primary(start, end, as_of=as_of, now=now, data_context=data_context)
    except mof_yield.MofDataError:
        points = []
        fallback_reason = "MOF primary retrieval unavailable"
    if not points:
        fallback = fred.fetch_series(_FRED_10Y, end.isoformat(), look_back_days, data_context=data_context)
        if not fallback or not fallback.get("points"):
            return None
        data = dict(fallback)
        data.update(
            series_id=key,
            title="Japan 10-year government bond yield",
            timing="FRED monthly fallback; observation-date filtered",
            actual_source="FRED",
            fallback_reason=fallback_reason,
        )
    else:
        data = {
            "series_id": key,
            "title": "Japan 10-year government bond yield",
            "units": "%",
            "frequency": "Daily",
            "seasonal": "",
            "start_date": start.isoformat(),
            "points": points,
            "timing": (
                "MOF daily constant-maturity yield; next-government-business-day "
                "09:30 JST publication; publication-time filtered"
            ),
            "actual_source": "Japan Ministry of Finance",
        }
    _series_cache.put_observation(cache_key, data, data_context=data_context)
    return data


def get_macro_report(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
    *,
    data_context: DataRequestContext,
) -> MacroReport:
    """Render JP macro data and retain the actual vendor for provenance."""
    if indicator.strip().lower() not in JP_SERIES:
        raise NoMarketDataError(indicator, detail="not a Japan macro series")
    data = fetch_series(indicator, curr_date, look_back_days, data_context=data_context)
    if data is None:
        return MacroReport(
            f"Japan macro: no data for '{indicator}' in this window.",
            "Japan macro",
            "available; no observations in requested window",
        )
    source = str(data["actual_source"])
    timing = f"{data['frequency']}; {data['timing']}"
    fallback_reason = data.get("fallback_reason")
    if fallback_reason:
        timing += f"; fallback: {fallback_reason}"
    return MacroReport(
        render_macro_report(f"Japan macro / {source}", data, curr_date),
        source,
        timing,
    )


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
    *,
    data_context: DataRequestContext,
) -> str:
    """Render one Japan macro series for the microscope tool."""
    return get_macro_report(indicator, curr_date, look_back_days, data_context=data_context).text
