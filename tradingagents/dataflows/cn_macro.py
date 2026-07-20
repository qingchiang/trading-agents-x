"""Keyless China macro series for the global panel and microscope tool.

The vendor uses bounded requests against Eastmoney's public macro/market-data
endpoints plus the National Bureau of Statistics release page for the latest
surveyed urban unemployment rate.  Series with only a reference period (CPI,
GDP and PMI) are explicitly non-vintage; trade/release-dated series are filtered
by their available date.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urljoin

import requests

from .cn.common import (
    REQUEST_TIMEOUT,
    AkShareRequestError,
    AkShareSchemaError,
    call_with_retry,
)
from .errors import NoMarketDataError
from .macro_common import SeriesCache, render_macro_report

DEFAULT_LOOKBACK_DAYS = 365
_UA = "Mozilla/5.0 trading-agents-x/0.3.1"
_EASTMONEY_DATA = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EASTMONEY_LEGACY = "https://datacenter.eastmoney.com/api/data/get"
_EASTMONEY_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_NBS_RELEASES = "https://www.stats.gov.cn/sj/zxfb/"
_SAFE_PARITY = "https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"
_CHINAMONEY_CURVE = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-currency/ClsYldCurvHis"


class _Series(NamedTuple):
    title: str
    units: str
    frequency: str
    timing: str


class _Fetched(NamedTuple):
    points: list[tuple[str, str]]
    actual_source: str
    frequency: str | None = None


CN_SERIES = {
    "cn_lpr": _Series("China 1-year loan prime rate", "%", "Monthly", "trade-date filtered"),
    "cn_10y_yield": _Series(
        "China 10-year government bond yield", "%", "Daily", "trade-date filtered"
    ),
    "cn_cpi": _Series(
        "China CPI inflation (YoY)",
        "%",
        "Monthly",
        "observation-period filtered; non-vintage",
    ),
    "cn_gdp": _Series(
        "China GDP growth (YoY)",
        "%",
        "Quarterly",
        "observation-period filtered; non-vintage",
    ),
    "cn_unemployment": _Series(
        "China surveyed urban unemployment rate",
        "%",
        "Monthly",
        "official release-date filtered; latest-release coverage",
    ),
    "cn_pmi": _Series(
        "China official manufacturing PMI",
        "index",
        "Monthly",
        "observation-period filtered; non-vintage",
    ),
    "usd_cny": _Series("USD/CNY central parity", "CNY per USD", "Daily", "trade-date filtered"),
}

_series_cache = SeriesCache(namespace="cn")


def timing_for(indicator: str) -> str:
    """Return the public timing contract for a China macro alias."""
    key = indicator.strip().lower()
    if key not in CN_SERIES:
        raise ValueError(f"Unknown China macro alias: {indicator!r}")
    return CN_SERIES[key].timing


def _request_json(url: str, *, label: str, params: dict) -> dict:
    def request():
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": _UA},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    payload = call_with_retry(request, label=label)
    if not isinstance(payload, dict):
        raise AkShareSchemaError(f"{label} returned an invalid JSON envelope.")
    return payload


def _request_text(url: str, *, label: str) -> str:
    def request():
        response = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text

    return call_with_retry(request, label=label)


def _post_text(url: str, *, label: str, data: dict) -> str:
    def request():
        response = requests.post(
            url,
            data=data,
            headers={"User-Agent": _UA},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text

    return call_with_retry(request, label=label)


def _eastmoney_rows(report_name: str, columns: str, sort_column: str) -> list[dict]:
    payload = _request_json(
        _EASTMONEY_DATA,
        label=f"Eastmoney China macro {report_name}",
        params={
            "reportName": report_name,
            "columns": columns,
            "pageNumber": 1,
            "pageSize": 500,
            "sortColumns": sort_column,
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
    )
    if payload.get("success") is False:
        if str(payload.get("code") or "") == "9201":
            return []
        raise AkShareRequestError(
            f"Eastmoney {report_name} rejected the request: "
            f"{payload.get('message') or 'unknown error'}"
        )
    result = payload.get("result")
    if not isinstance(result, dict) or "data" not in result:
        raise AkShareSchemaError(f"Eastmoney {report_name} response is missing data.")
    rows = result["data"]
    if rows is None:
        return []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AkShareSchemaError(f"Eastmoney {report_name} returned invalid rows.")
    return rows


def _date_value_points(
    rows: list[dict], date_field: str, value_field: str, start: date, end: date
) -> list[tuple[str, str]]:
    points: dict[str, str] = {}
    for row in rows:
        parsed_date = _parse_date(row.get(date_field))
        value = _numeric(row.get(value_field))
        if parsed_date is None or value is None or not start <= parsed_date <= end:
            continue
        points[parsed_date.isoformat()] = value
    return sorted(points.items())


def _parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).split(" ", 1)[0]).date()
    except ValueError:
        return None


def _numeric(value) -> str | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return f"{number:g}"


def _fetch_lpr(start: date, end: date) -> list[tuple[str, str]]:
    rows = _eastmoney_rows("RPTA_WEB_RATE", "TRADE_DATE,LPR1Y", "TRADE_DATE")
    return _date_value_points(rows, "TRADE_DATE", "LPR1Y", start, end)


def _fetch_economy(
    report_name: str, value_field: str, start: date, end: date
) -> list[tuple[str, str]]:
    rows = _eastmoney_rows(
        report_name,
        f"REPORT_DATE,{value_field}",
        "REPORT_DATE",
    )
    return _date_value_points(rows, "REPORT_DATE", value_field, start, end)


def _fetch_10y_eastmoney(start: date, end: date) -> list[tuple[str, str]]:
    payload = _request_json(
        _EASTMONEY_LEGACY,
        label="Eastmoney China 10-year government bond yield",
        params={
            "type": "RPTA_WEB_TREASURYYIELD",
            "sty": "SOLAR_DATE,EMM00166466",
            "st": "SOLAR_DATE",
            "sr": -1,
            "p": 1,
            "ps": 500,
        },
    )
    result = payload.get("result")
    if not isinstance(result, dict) or "data" not in result:
        raise AkShareSchemaError("Eastmoney China bond-yield response is missing data.")
    rows = result["data"] or []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AkShareSchemaError("Eastmoney China bond-yield response has invalid rows.")
    return _date_value_points(rows, "SOLAR_DATE", "EMM00166466", start, end)


def _fetch_10y_chinamoney(start: date, end: date) -> list[tuple[str, str]]:
    query_start = max(start, end - timedelta(days=14))
    payload = _request_json(
        _CHINAMONEY_CURVE,
        label="ChinaMoney government bond yield curve",
        params={
            "lang": "CN",
            "reference": "1,2,3",
            "bondType": "CYCC000",
            "startDate": query_start.isoformat(),
            "endDate": end.isoformat(),
            "pageNum": 1,
            # The public endpoint rejects larger page sizes. Rows are newest-first
            # and one date's 10Y tenor is within the first 50, so one request is
            # sufficient for the latest-value fallback.
            "pageSize": 50,
            "termId": 1,
        },
    )
    records = payload.get("records")
    if records is None:
        return []
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise AkShareSchemaError("ChinaMoney yield-curve response has invalid rows.")
    points = {}
    for row in records:
        try:
            tenor = float(row.get("yearTermStr"))
        except (TypeError, ValueError):
            continue
        observation = _parse_date(row.get("newDateValueCN"))
        value = _numeric(row.get("maturityYieldStr"))
        if tenor == 10.0 and observation and value and start <= observation <= end:
            points[observation.isoformat()] = value
    if not points:
        return []
    latest = max(points)
    return [(latest, points[latest])]


def _fetch_10y(start: date, end: date) -> _Fetched:
    try:
        points = _fetch_10y_eastmoney(start, end)
        if points:
            return _Fetched(points, "Eastmoney")
    except AkShareRequestError:
        pass
    return _Fetched(
        _fetch_10y_chinamoney(start, end),
        "China Foreign Exchange Trade System",
        "Latest official curve snapshot",
    )


class _SafeTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _fetch_usd_cny_safe(start: date, end: date) -> list[tuple[str, str]]:
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    raw = _post_text(
        _SAFE_PARITY,
        label="SAFE RMB central parity",
        data={"startDate": start.isoformat(), "endDate": end.isoformat(), "queryYN": "true"},
    )
    parser = _SafeTableParser()
    parser.feed(raw)
    header_index = next(
        (index for index, row in enumerate(parser.rows) if "日期" in row and "美元" in row),
        None,
    )
    if header_index is None:
        raise AkShareSchemaError("SAFE central-parity response is missing the date/USD header.")
    header = parser.rows[header_index]
    date_column = header.index("日期")
    usd_column = header.index("美元")
    points = {}
    for row in parser.rows[header_index + 1 :]:
        if len(row) <= max(date_column, usd_column):
            continue
        observation = _parse_date(row[date_column])
        value = _numeric(row[usd_column])
        if observation is None or value is None or not start <= observation <= end:
            continue
        # SAFE quotes RMB per 100 USD; the public alias is CNY per one USD.
        points[observation.isoformat()] = f"{float(value) / 100:g}"
    return sorted(points.items())


def _fetch_usd_cny_eastmoney(start: date, end: date) -> list[tuple[str, str]]:
    payload = _request_json(
        _EASTMONEY_KLINE,
        label="Eastmoney USD/CNY central parity",
        params={
            "secid": "120.USDCNYC",
            "klt": 101,
            "fqt": 1,
            "lmt": 500,
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "forcect": 1,
        },
    )
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
        raise AkShareSchemaError("Eastmoney USD/CNY response has invalid kline data.")
    points = []
    for raw in data["klines"]:
        fields = str(raw).split(",")
        if len(fields) < 3:
            raise AkShareSchemaError("Eastmoney USD/CNY kline changed schema.")
        parsed_date = _parse_date(fields[0])
        value = _numeric(fields[2])
        if parsed_date is not None and value is not None and start <= parsed_date <= end:
            points.append((parsed_date.isoformat(), value))
    return sorted(dict(points).items())


def _fetch_usd_cny(start: date, end: date) -> _Fetched:
    try:
        points = _fetch_usd_cny_safe(start, end)
        if points:
            return _Fetched(points, "SAFE")
    except AkShareRequestError:
        pass
    return _Fetched(_fetch_usd_cny_eastmoney(start, end), "Eastmoney")


class _ReleaseLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            if text:
                self.links.append((text, self._href))
            self._href = None
            self._text = []


def _plain_text(raw_html: str) -> str:
    text = re.sub(
        r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", raw_html, flags=re.I | re.S
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def _fetch_unemployment(start: date, end: date) -> list[tuple[str, str]]:
    listing = _request_text(_NBS_RELEASES, label="NBS data-release index")
    parser = _ReleaseLinkParser()
    parser.feed(listing)
    seen: set[str] = set()
    candidates = []
    for title, href in parser.links:
        if "经济运行" not in title:
            continue
        url = urljoin(_NBS_RELEASES, href)
        if url in seen:
            continue
        seen.add(url)
        match = re.search(r"t(\d{8})_", url)
        if not match:
            continue
        release_date = datetime.strptime(match.group(1), "%Y%m%d").date()
        if release_date <= end:
            candidates.append((release_date, url))

    for release_date, url in sorted(candidates, reverse=True)[:3]:
        article = _plain_text(_request_text(url, label="NBS economy release"))
        match = re.search(
            r"(\d{1,2})\s*月份?，?\s*全国城镇调查失业率为\s*([0-9.]+)\s*%",
            article,
        )
        if not match:
            continue
        month = int(match.group(1))
        year = release_date.year - 1 if month > release_date.month else release_date.year
        observation = date(year, month, 1)
        if start <= observation <= end:
            return [(observation.isoformat(), match.group(2))]
    return []


def fetch_series(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> dict | None:
    """Fetch one China macro alias in the shared structured-series shape."""
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS
    key = indicator.strip().lower()
    if key not in CN_SERIES:
        raise ValueError(
            f"'{indicator}' is not a known China macro alias. "
            f"Known aliases: {', '.join(sorted(CN_SERIES))}."
        )
    cache_key = (key, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    start = end - timedelta(days=look_back_days)
    fetchers = {
        "cn_lpr": _fetch_lpr,
        "cn_10y_yield": _fetch_10y,
        "cn_cpi": lambda s, e: _fetch_economy("RPT_ECONOMY_CPI", "NATIONAL_SAME", s, e),
        "cn_gdp": lambda s, e: _fetch_economy("RPT_ECONOMY_GDP", "SUM_SAME", s, e),
        "cn_unemployment": _fetch_unemployment,
        "cn_pmi": lambda s, e: _fetch_economy("RPT_ECONOMY_PMI", "MAKE_INDEX", s, e),
        "usd_cny": _fetch_usd_cny,
    }
    fetched = fetchers[key](start, end)
    if isinstance(fetched, _Fetched):
        points = fetched.points
        actual_source = fetched.actual_source
        frequency = fetched.frequency
    else:
        points = fetched
        actual_source = None
        frequency = None
    if not points:
        return None
    spec = CN_SERIES[key]
    data = {
        "series_id": key,
        "title": spec.title,
        "units": spec.units,
        "frequency": frequency or spec.frequency,
        "seasonal": "",
        "start_date": start.isoformat(),
        "points": points,
        "timing": f"{actual_source}; {spec.timing}" if actual_source else spec.timing,
    }
    if actual_source:
        data["actual_source"] = actual_source
    _series_cache.put(cache_key, data)
    return data


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Render one China macro series for the microscope tool."""
    if indicator.strip().lower() not in CN_SERIES:
        raise NoMarketDataError(indicator, detail="not a China macro series")
    data = fetch_series(indicator, curr_date, look_back_days)
    if data is None:
        return f"China macro: no data for '{indicator}' in this window."
    return render_macro_report("China macro", data, curr_date)
