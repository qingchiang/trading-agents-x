"""Chinese Google News RSS headlines for mainland equities."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

from tradingagents.version import USER_AGENT

from ..errors import VendorRateLimitError
from ..news_quality import (
    build_chinese_company_aliases,
    canonical_headline,
    classify_chinese_google_article,
)
from ..rate_limit import stop_on_rate_limit_requested
from .common import REQUEST_TIMEOUT, AkShareSchemaError, call_with_retry, canonical_a_share
from .company import get_company_profile
from .news_sources import news_quotas

_RSS = "https://news.google.com/rss/search"
_CST = timezone(timedelta(hours=8))
_UA = USER_AGENT
logger = logging.getLogger(__name__)


def _company_names(ticker: str) -> tuple[str | None, str | None]:
    profile = get_company_profile(ticker)
    if profile.empty:
        return None, None
    row = profile.iloc[0]
    full_value = row.get("公司名称")
    short_value = row.get("A股简称")
    full = None if pd.isna(full_value) else str(full_value).strip() or None
    short = None if pd.isna(short_value) else str(short_value).strip() or None
    return full, short


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        value = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_CST).replace(tzinfo=None)


def _fetch_items(query: str) -> list[dict]:
    def request():
        response = requests.get(
            _RSS,
            params={"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans", "q": query},
            headers={"User-Agent": _UA},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.content

    raw = call_with_retry(request, label=f"Google News China {query!r}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AkShareSchemaError("Google News China returned invalid RSS XML.") from exc
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        source_element = item.find("source")
        source = ((source_element.text if source_element is not None else "") or "").strip()
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
        items.append(
            {
                "title": title,
                "source": source or "Unknown",
                "published": _parse_date(item.findtext("pubDate")),
            }
        )
    return items


def _safe_query(query: str) -> tuple[list[dict], Exception | None]:
    """Keep another name query usable when this independent query fails."""
    try:
        return _fetch_items(query), None
    except VendorRateLimitError as exc:
        if stop_on_rate_limit_requested():
            raise
        logger.warning("Google News China query rate-limited for %r", query)
        return [], exc
    except Exception as exc:  # noqa: BLE001 - query-level isolation boundary
        logger.warning("Google News China query failed for %r: %s", query, exc)
        return [], exc


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return entity-filtered Chinese media headlines within CST calendar days."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    full_name, short_name = _company_names(ticker)
    # Query both ordinary headline form (short name) and the legal name. The
    # code biases ranking but never establishes direct evidence by itself.
    query_names = tuple(dict.fromkeys(name for name in (short_name, full_name) if name))
    if not query_names:
        return f"No Google News China coverage identity for {ticker}"
    queries = tuple(f'"{name}" {code} 股票' for name in query_names)
    if stop_on_rate_limit_requested():
        query_results = [_safe_query(query) for query in queries]
    else:
        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            query_results = list(pool.map(_safe_query, queries))
    failures = [exc for _items, exc in query_results if exc is not None]
    if len(failures) == len(query_results):
        raise failures[0]
    candidates = [item for feed, _exc in query_results for item in feed]
    items = [
        item
        for item in candidates
        if item["title"]
        and item["published"] is not None
        and start <= item["published"] < end + timedelta(days=1)
    ]
    aliases = build_chinese_company_aliases(code, full_name, short_name)
    items.sort(key=lambda item: item["published"], reverse=True)
    seen: set[str] = set()
    relevant = []
    for item in items:
        classification = classify_chinese_google_article(item["title"], item["source"], aliases)
        key = canonical_headline(item["title"])
        if classification.tier == "drop" or not key or key in seen:
            continue
        seen.add(key)
        relevant.append((item, classification.tier))

    _disclosure_limit, _research_limit, media_limit = news_quotas()
    kept = relevant[:media_limit]
    if not kept:
        if failures:
            return (
                f"<Google News China partially unavailable: {len(failures)} of "
                f"{len(query_results)} name queries failed; successful queries returned "
                "no relevant items>"
            )
        return (
            f"No relevant Google News China found for {ticker} between {start_date} "
            f"and {end_date} after quality filtering ({len(items)} candidates dropped)"
        )
    body = "\n\n".join(
        f"### [{tier}] {item['title']} (source: {item['source']})\n"
        f"Published: {item['published'].strftime('%Y-%m-%d %H:%M')} CST"
        for item, tier in kept
    )
    availability = ""
    if failures:
        availability = (
            f"\n\nQuery availability note: {len(failures)} of "
            f"{len(query_results)} Google News name queries failed."
        )
    return (
        f"## {ticker} media headlines (Google News China), from {start_date} "
        f"to {end_date}:\n\n{body}{availability}"
    )
