"""Keyless Japanese market macro series with explicit source fallback."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import NamedTuple

import requests

from . import fred
from .cn.common import (
    REQUEST_TIMEOUT,
    AkShareRequestError,
    AkShareSchemaError,
    call_with_retry,
)
from .errors import NoMarketDataError
from .jp.calendar import completed_market_date
from .macro_common import SeriesCache, render_macro_report

_EASTMONEY_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_SUGGEST = "https://searchapi.eastmoney.com/api/suggest/get"
_STATIC_QUOTE_ID = "171.JP10Y"
_FRED_10Y = "IRLTLT01JPM156N"
_UA = "Mozilla/5.0 trading-agents-x/0.3.1"

JP_SERIES = {"jp_10y_yield"}
_series_cache = SeriesCache(namespace="jp")


class MacroReport(NamedTuple):
    text: str
    source: str
    timing: str


def _request_json(url: str, *, label: str, params: dict) -> dict:
    def request():
        response = requests.get(
            url, params=params, headers={"User-Agent": _UA}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()

    payload = call_with_retry(request, label=label)
    if not isinstance(payload, dict):
        raise AkShareSchemaError(f"{label} returned an invalid JSON envelope.")
    return payload


def _validate_identity(data: dict, quote_id: str) -> None:
    market, expected_code = quote_id.split(".", 1)
    code = str(data.get("code") or "").upper()
    response_market = str(data.get("market") or "")
    name = str(data.get("name") or "")
    if code != expected_code or market != "171" or response_market != market:
        raise AkShareSchemaError("Eastmoney JP10Y returned the wrong security identity.")
    if "日本" not in name or "10年" not in name or not ("国债" in name or "国債" in name):
        raise AkShareSchemaError("Eastmoney JP10Y returned an unexpected security name.")


def _fetch_eastmoney(quote_id: str, start, end) -> list[tuple[str, str]]:
    payload = _request_json(
        _EASTMONEY_KLINE,
        label="Eastmoney Japan 10-year government bond yield",
        params={
            "secid": quote_id,
            "klt": 101,
            "fqt": 1,
            "lmt": 500,
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56",
        },
    )
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
        raise AkShareSchemaError("Eastmoney JP10Y response has invalid kline data.")
    _validate_identity(data, quote_id)
    points = {}
    for row in data["klines"]:
        fields = str(row).split(",")
        if len(fields) < 3:
            raise AkShareSchemaError("Eastmoney JP10Y kline changed schema.")
        try:
            observation = datetime.strptime(fields[0], "%Y-%m-%d").date()
            value = float(fields[2])
        except (TypeError, ValueError) as exc:
            raise AkShareSchemaError("Eastmoney JP10Y returned an invalid value.") from exc
        if not math.isfinite(value):
            raise AkShareSchemaError("Eastmoney JP10Y returned a non-finite value.")
        if start <= observation <= end:
            points[observation.isoformat()] = f"{value:g}"
    return sorted(points.items())


def _resolve_quote_id() -> str:
    payload = _request_json(
        _EASTMONEY_SUGGEST,
        label="Eastmoney JP10Y code search",
        params={"input": "JP10Y", "type": 14},
    )
    table = payload.get("QuotationCodeTable")
    rows = table.get("Data") if isinstance(table, dict) else None
    if not isinstance(rows, list):
        raise AkShareSchemaError("Eastmoney JP10Y code search response changed schema.")
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("Code") or "").upper()
        name = str(row.get("Name") or "")
        security_type = str(row.get("SecurityTypeName") or "")
        quote_id = str(row.get("QuoteID") or "")
        if (
            code == "JP10Y"
            and quote_id.startswith("171.")
            and "债券" in security_type
            and "日本" in name
            and "10年" in name
        ):
            return quote_id
    raise AkShareSchemaError("Eastmoney JP10Y code search found no validated bond.")


def _fetch_primary(start, end) -> list[tuple[str, str]]:
    try:
        points = _fetch_eastmoney(_STATIC_QUOTE_ID, start, end)
        if points:
            return points
    except AkShareRequestError:  # static mapping is only a hint; search self-heals
        pass
    quote_id = _resolve_quote_id()
    return _fetch_eastmoney(quote_id, start, end)


def fetch_series(indicator: str, curr_date: str, look_back_days: int | None = None) -> dict | None:
    """Fetch JP10Y from Eastmoney daily data, then FRED monthly fallback."""
    key = indicator.strip().lower()
    if key not in JP_SERIES:
        raise ValueError(f"Unknown Japan macro alias: {indicator!r}")
    look_back_days = 365 if look_back_days is None else look_back_days
    cache_key = (key, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    requested_end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    end = completed_market_date(requested_end)
    start = end - timedelta(days=look_back_days)
    try:
        points = _fetch_primary(start, end)
    except AkShareRequestError:
        points = []
    if not points:
        fallback = fred.fetch_series(_FRED_10Y, end.isoformat(), look_back_days)
        if not fallback or not fallback.get("points"):
            return None
        data = dict(fallback)
        data.update(
            series_id=key,
            title="Japan 10-year government bond yield",
            timing="FRED monthly fallback; observation-date filtered",
            actual_source="FRED",
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
            "timing": "Eastmoney daily; trade-date filtered; 17:00 Tokyo cutoff",
            "actual_source": "Eastmoney",
        }
    _series_cache.put(cache_key, data)
    return data


def get_macro_report(
    indicator: str, curr_date: str, look_back_days: int | None = None
) -> MacroReport:
    """Render JP macro data and retain the actual vendor for provenance."""
    if indicator.strip().lower() not in JP_SERIES:
        raise NoMarketDataError(indicator, detail="not a Japan macro series")
    data = fetch_series(indicator, curr_date, look_back_days)
    if data is None:
        return MacroReport(
            f"Japan macro: no data for '{indicator}' in this window.",
            "Japan macro",
            "available; no observations in requested window",
        )
    source = str(data["actual_source"])
    return MacroReport(
        render_macro_report(f"Japan macro / {source}", data, curr_date),
        source,
        f"{data['frequency']}; {data['timing']}",
    )


def get_macro_data(indicator: str, curr_date: str, look_back_days: int | None = None) -> str:
    """Render one Japan macro series for the microscope tool."""
    return get_macro_report(indicator, curr_date, look_back_days).text
