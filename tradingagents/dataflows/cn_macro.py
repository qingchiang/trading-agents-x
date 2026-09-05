"""Keyless China macro series for the global panel and microscope tool.

CPI, GDP, PMI, and unemployment prefer bounded National Bureau of Statistics
release-page retrieval so recent readings have an auditable publication date.
CPI/GDP/PMI fall back to Eastmoney's observation-period-only, non-vintage
series when a recent official release is not discoverable.  Market series keep
their existing SAFE/Eastmoney/ChinaMoney chains and are filtered by trade date.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import urljoin

import requests

from tradingagents.version import BROWSER_USER_AGENT

from .cn.common import (
    REQUEST_TIMEOUT,
    AkShareRateLimitError,
    AkShareRequestError,
    AkShareSchemaError,
    call_with_retry,
)
from .errors import NoMarketDataError
from .macro_common import SeriesCache, render_macro_report

DEFAULT_LOOKBACK_DAYS = 365
_UA = BROWSER_USER_AGENT
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
    fallback_reason: str | None = None
    timing: str | None = None
    metadata: dict[str, str] | None = None


class _NbsRelease(NamedTuple):
    title: str
    url: str
    release_date: date


class _NbsObservation(NamedTuple):
    point: tuple[str, str]
    observation_period: str
    growth_basis: str | None = None


class MacroReport(NamedTuple):
    text: str
    source: str
    timing: str


CN_SERIES = {
    "cn_lpr": _Series("China 1-year loan prime rate", "%", "Monthly", "trade-date filtered"),
    "cn_10y_yield": _Series(
        "China 10-year government bond yield", "%", "Daily", "trade-date filtered"
    ),
    "cn_cpi": _Series(
        "China CPI inflation (YoY)",
        "%",
        "Monthly",
        "official release-date filtered when available; Eastmoney fallback is non-vintage",
    ),
    "cn_gdp": _Series(
        "China GDP growth (YoY)",
        "%",
        "Quarterly",
        "official release-date filtered when available; Eastmoney fallback is non-vintage",
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
        "official release-date filtered when available; Eastmoney fallback is non-vintage",
    ),
    "usd_cny": _Series("USD/CNY central parity", "CNY per USD", "Daily", "trade-date filtered"),
}

_series_cache = SeriesCache(namespace="cn")
_nbs_index_cache = SeriesCache(max_entries=32, namespace="cn_nbs_index")

_NBS_SOURCE = "National Bureau of Statistics of China"
_NBS_INDEX_PAGES = (_NBS_RELEASES, urljoin(_NBS_RELEASES, "index_1.html"))
_NBS_TIMING = "official release-date filtered; latest-release coverage"
_EASTMONEY_NON_VINTAGE_TIMING = "observation-period filtered; non-vintage"
_NBS_CHAIN_CACHE_VERSION = "nbs-release-v1"


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
    fallback_reason = "Eastmoney returned no usable observations"
    try:
        points = _fetch_10y_eastmoney(start, end)
        if points:
            return _Fetched(points, "Eastmoney")
    except (AkShareRequestError, AkShareRateLimitError):
        fallback_reason = "Eastmoney primary retrieval unavailable"
    return _Fetched(
        _fetch_10y_chinamoney(start, end),
        "China Foreign Exchange Trade System",
        "Latest official curve snapshot",
        fallback_reason,
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
    fallback_reason = "SAFE returned no usable observations"
    try:
        points = _fetch_usd_cny_safe(start, end)
        if points:
            return _Fetched(points, "SAFE")
    except (AkShareRequestError, AkShareRateLimitError):
        fallback_reason = "SAFE primary retrieval unavailable"
    return _Fetched(
        _fetch_usd_cny_eastmoney(start, end),
        "Eastmoney",
        fallback_reason=fallback_reason,
    )


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


def _release_date_from_url(url: str) -> date | None:
    match = re.search(r"t(\d{8})_", url)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _nbs_release_page(page_index: int, end: date) -> list[_NbsRelease]:
    """Return one bounded NBS release-index page, cached per analysis date."""
    cache_key = (f"release-index-{page_index}", end.isoformat(), 0)
    cached = _nbs_index_cache.get(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("releases"), list):
        releases = []
        for row in cached["releases"]:
            if not isinstance(row, dict) or "title" not in row or "url" not in row:
                continue
            release_date = _parse_date(row.get("release_date"))
            if release_date is not None:
                releases.append(_NbsRelease(row["title"], row["url"], release_date))
        if releases:
            return releases

    url = _NBS_INDEX_PAGES[page_index]
    listing = _request_text(url, label=f"NBS data-release index page {page_index + 1}")
    parser = _ReleaseLinkParser()
    parser.feed(listing)
    releases_by_url: dict[str, _NbsRelease] = {}
    for title, href in parser.links:
        article_url = urljoin(_NBS_RELEASES, href)
        release_date = _release_date_from_url(article_url)
        if release_date is None:
            continue
        candidate = _NbsRelease(title, article_url, release_date)
        existing = releases_by_url.get(article_url)
        if existing is None or len(candidate.title) > len(existing.title):
            releases_by_url[article_url] = candidate
    releases = list(releases_by_url.values())
    if not releases:
        raise AkShareSchemaError("NBS data-release index is missing dated article links.")
    releases.sort(key=lambda item: item.release_date, reverse=True)
    _nbs_index_cache.put(
        cache_key,
        {
            "releases": [
                {
                    "title": item.title,
                    "url": item.url,
                    "release_date": item.release_date.isoformat(),
                }
                for item in releases
            ]
        },
    )
    return releases


def _find_nbs_release(indicator: str, end: date) -> _NbsRelease | None:
    title_terms = {
        "cn_cpi": ("居民消费价格同比",),
        "cn_gdp": ("国内生产总值初步核算结果",),
        "cn_pmi": ("中国采购经理指数运行情况",),
    }
    terms = title_terms[indicator]
    for page_index in range(len(_NBS_INDEX_PAGES)):
        candidates = [
            item
            for item in _nbs_release_page(page_index, end)
            if item.release_date <= end and all(term in item.title for term in terms)
        ]
        if candidates:
            return max(candidates, key=lambda item: item.release_date)
    return None


def _parse_nbs_cpi(title: str, article: str) -> _NbsObservation:
    period = re.search(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月份?", title)
    value = re.search(
        r"(?:全国)?居民消费价格同比"
        r"(?:(?P<direction>上涨|下降)\s*(?P<value>[0-9.]+)\s*%|(?P<flat>持平))",
        article,
    )
    if not period or not value:
        raise AkShareSchemaError("NBS CPI release is missing the national YoY headline.")
    year = int(period.group("year"))
    month = int(period.group("month"))
    raw_value = 0.0 if value.group("flat") else float(value.group("value"))
    numeric_value = -raw_value if value.group("direction") == "下降" else raw_value
    if not -100.0 <= numeric_value <= 100.0:
        raise AkShareSchemaError("NBS CPI headline value is outside percentage bounds.")
    try:
        observation = date(year, month, 1)
    except ValueError as exc:
        raise AkShareSchemaError("NBS CPI release has an invalid reference month.") from exc
    return _NbsObservation(
        (observation.isoformat(), f"{numeric_value:g}"),
        f"{year:04d}-{month:02d}",
    )


def _parse_nbs_pmi(title: str, article: str) -> _NbsObservation:
    period = re.search(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月", title)
    value = re.search(
        r"\d{1,2}\s*月份?，?\s*制造业采购经理指数\s*"
        r"(?:（\s*PMI\s*）)?\s*为\s*([0-9.]+)\s*%",
        article,
    )
    if not period or not value:
        raise AkShareSchemaError("NBS PMI release is missing its period or headline value.")
    year = int(period.group("year"))
    month = int(period.group("month"))
    numeric_value = float(value.group(1))
    if not 0.0 <= numeric_value <= 100.0:
        raise AkShareSchemaError("NBS PMI headline value is outside index bounds.")
    try:
        observation = date(year, month, 1)
    except ValueError as exc:
        raise AkShareSchemaError("NBS PMI release has an invalid reference month.") from exc
    return _NbsObservation(
        (observation.isoformat(), f"{numeric_value:g}"),
        f"{year:04d}-{month:02d}",
    )


def _parse_nbs_gdp(title: str, raw_html: str) -> _NbsObservation:
    period = re.search(r"(?P<year>20\d{2})年(?P<quarter>[一二三四])季度", title)
    if not period:
        raise AkShareSchemaError("NBS GDP release title is missing the reference quarter.")
    parser = _SafeTableParser()
    parser.feed(raw_html)
    gdp_rows = [row for row in parser.rows if row and row[0].strip().upper() == "GDP"]
    if not gdp_rows:
        raise AkShareSchemaError("NBS GDP release is missing the headline GDP table row.")
    values = [_numeric(value) for value in gdp_rows[0][1:]]
    numeric_values = [value for value in values if value is not None]
    year = int(period.group("year"))
    quarter = {"一": 1, "二": 2, "三": 3, "四": 4}[period.group("quarter")]
    expected_values = 2 if quarter == 1 else 4
    if len(numeric_values) != expected_values:
        raise AkShareSchemaError("NBS GDP headline row changed its expected column count.")
    yoy_value = float(numeric_values[-1])
    if not -100.0 <= yoy_value <= 100.0:
        raise AkShareSchemaError("NBS GDP headline value is outside percentage bounds.")
    month = quarter * 3
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    observation = next_month - timedelta(days=1)
    period_labels = {
        1: f"{year} Q1 / year-to-date",
        2: f"{year} H1",
        3: f"{year} Q1-Q3",
        4: f"{year} full year",
    }
    growth_basis = "cumulative year-to-date YoY"
    if quarter == 1:
        growth_basis += " (same as single-quarter YoY for Q1)"
    return _NbsObservation(
        (observation.isoformat(), f"{yoy_value:g}"),
        period_labels[quarter],
        growth_basis,
    )


def _fetch_nbs_indicator(indicator: str, start: date, end: date) -> _Fetched | None:
    release = _find_nbs_release(indicator, end)
    if release is None:
        return None
    raw_html = _request_text(release.url, label=f"NBS {indicator} release")
    article = _plain_text(raw_html)
    parsers = {
        "cn_cpi": lambda: _parse_nbs_cpi(release.title, article),
        "cn_gdp": lambda: _parse_nbs_gdp(release.title, raw_html),
        "cn_pmi": lambda: _parse_nbs_pmi(release.title, article),
    }
    parsed = parsers[indicator]()
    observation = _parse_date(parsed.point[0])
    if observation is None or not start <= observation <= end:
        return None
    metadata = {
        "release_date": release.release_date.isoformat(),
        "observation_period": parsed.observation_period,
    }
    if parsed.growth_basis:
        metadata["growth_basis"] = parsed.growth_basis
    return _Fetched(
        [parsed.point],
        _NBS_SOURCE,
        timing=_NBS_TIMING,
        metadata=metadata,
    )


def _fetch_economy_chain(
    indicator: str,
    report_name: str,
    value_field: str,
    start: date,
    end: date,
) -> _Fetched:
    fallback_reason = "NBS returned no usable recent official release"
    primary_failed = False
    try:
        official = _fetch_nbs_indicator(indicator, start, end)
        if official is not None and official.points:
            return official
    except AkShareSchemaError:
        primary_failed = True
        fallback_reason = "NBS primary response schema changed"
    except (AkShareRequestError, AkShareRateLimitError):
        primary_failed = True
        fallback_reason = "NBS primary retrieval unavailable"
    fallback_points = _fetch_economy(report_name, value_field, start, end)
    if primary_failed and not fallback_points:
        raise AkShareRequestError(
            f"China macro {indicator} retrieval unavailable: {fallback_reason}; "
            "Eastmoney returned no usable observations."
        )
    return _Fetched(
        fallback_points,
        "Eastmoney",
        fallback_reason=fallback_reason,
        timing=_EASTMONEY_NON_VINTAGE_TIMING,
    )


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
    cache_series_id = (
        f"{key}:{_NBS_CHAIN_CACHE_VERSION}"
        if key in {"cn_cpi", "cn_gdp", "cn_pmi"}
        else key
    )
    cache_key = (cache_series_id, curr_date, look_back_days)
    cached = _series_cache.get(cache_key)
    if cached is not None:
        return cached

    end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    start = end - timedelta(days=look_back_days)
    fetchers = {
        "cn_lpr": _fetch_lpr,
        "cn_10y_yield": _fetch_10y,
        "cn_cpi": lambda s, e: _fetch_economy_chain(
            "cn_cpi", "RPT_ECONOMY_CPI", "NATIONAL_SAME", s, e
        ),
        "cn_gdp": lambda s, e: _fetch_economy_chain(
            "cn_gdp", "RPT_ECONOMY_GDP", "SUM_SAME", s, e
        ),
        "cn_unemployment": _fetch_unemployment,
        "cn_pmi": lambda s, e: _fetch_economy_chain(
            "cn_pmi", "RPT_ECONOMY_PMI", "MAKE_INDEX", s, e
        ),
        "usd_cny": _fetch_usd_cny,
    }
    fetched = fetchers[key](start, end)
    if isinstance(fetched, _Fetched):
        points = fetched.points
        actual_source = fetched.actual_source
        frequency = fetched.frequency
        fallback_reason = fetched.fallback_reason
        source_timing = fetched.timing
        metadata = fetched.metadata or {}
    else:
        points = fetched
        actual_source = None
        frequency = None
        fallback_reason = None
        source_timing = None
        metadata = {}
    if not points:
        return None
    spec = CN_SERIES[key]
    timing = source_timing or spec.timing
    if metadata:
        timing_details = [
            f"release date={metadata['release_date']}",
            f"observation period={metadata['observation_period']}",
        ]
        if metadata.get("growth_basis"):
            timing_details.append(f"growth basis={metadata['growth_basis']}")
        timing = f"{timing}; {'; '.join(timing_details)}"
    data = {
        "series_id": key,
        "title": spec.title,
        "units": spec.units,
        "frequency": frequency or spec.frequency,
        "seasonal": "",
        "start_date": start.isoformat(),
        "points": points,
        "timing": f"{actual_source}; {timing}" if actual_source else timing,
    }
    if actual_source:
        data["actual_source"] = actual_source
    if fallback_reason:
        data["fallback_reason"] = fallback_reason
    data.update(metadata)
    _series_cache.put_observation(cache_key, data)
    return data


def get_macro_report(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> MacroReport:
    """Render China macro data and retain actual-source fallback metadata."""
    if indicator.strip().lower() not in CN_SERIES:
        raise NoMarketDataError(indicator, detail="not a China macro series")
    data = fetch_series(indicator, curr_date, look_back_days)
    if data is None:
        return MacroReport(
            f"China macro: no data for '{indicator}' in this window.",
            "China macro",
            "available; no observations in requested window",
        )
    source = str(data.get("actual_source") or "China macro")
    data_timing = str(data.get("timing") or timing_for(indicator))
    timing = f"{data['frequency']}; {data_timing}"
    fallback_reason = data.get("fallback_reason")
    if fallback_reason:
        timing += f"; fallback: {fallback_reason}"
    return MacroReport(
        render_macro_report(
            "China macro" if source == "China macro" else f"China macro / {source}",
            data,
            curr_date,
        ),
        source,
        timing,
    )


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
) -> str:
    """Render one China macro series for the microscope tool."""
    return get_macro_report(indicator, curr_date, look_back_days).text
