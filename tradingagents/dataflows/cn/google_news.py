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
from ..news_diagnostics import CandidateFilterCounts
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
    counts = CandidateFilterCounts()
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    full_name, short_name = _company_names(ticker)
    # Query both ordinary headline form (short name) and the legal name. The
    # code biases ranking but never establishes direct evidence by itself.
    query_names = tuple(dict.fromkeys(name for name in (short_name, full_name) if name))
    if not query_names:
        return f"No Google News China coverage identity for {ticker}\n{counts.render()}"
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
    counts.upstream_returned = len(candidates)
    items = []
    for item in candidates:
        if not item["title"] or item["published"] is None:
            counts.invalid_records += 1
            continue
        if not start <= item["published"] < end + timedelta(days=1):
            counts.date_filtered += 1
            continue
        items.append(item)
    aliases = build_chinese_company_aliases(code, full_name, short_name)
    items.sort(key=lambda item: item["published"], reverse=True)
    seen: set[str] = set()
    relevant = []
    for item in items:
        classification = classify_chinese_google_article(item["title"], item["source"], aliases)
        key = canonical_headline(item["title"])
        if classification.tier == "drop":
            counts.relevance_filtered += 1
            continue
        if not key:
            counts.invalid_records += 1
            continue
        if key in seen:
            counts.duplicates += 1
            continue
        seen.add(key)
        relevant.append((item, classification.tier))

    _disclosure_limit, _research_limit, media_limit = news_quotas()
    kept = relevant[:media_limit]
    counts.source_truncated = len(relevant) - len(kept)
    if not kept:
        if failures:
            return (
                f"<Google News China partially unavailable: {len(failures)} of "
                f"{len(query_results)} name queries failed; successful queries returned "
                f"no relevant items>\n{counts.render()}"
            )
        return (
            f"No relevant Google News China found for {ticker} between {start_date} "
            f"and {end_date} after quality filtering ({len(items)} candidates dropped)"
            f"\n{counts.render()}"
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
        f"to {end_date}:\n\n{counts.render()}{availability}\n\n{body}"
    )
