"""Bounded A-share disclosure and sell-side research feeds."""

from __future__ import annotations

import html
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from threading import RLock
from urllib.parse import urlencode

import pandas as pd
import requests

from ..config import get_config
from ..news_quality import canonical_headline
from .common import REQUEST_TIMEOUT, AkShareSchemaError, call_with_retry, canonical_a_share

_CNINFO_STOCKS = "https://www.cninfo.com.cn/new/data/szse_stock.json"
_CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_DETAIL = "https://www.cninfo.com.cn/new/disclosure/detail"
_RESEARCH_QUERY = "https://reportapi.eastmoney.com/report/list"
_TITLE_TAG = re.compile(r"<[^>]+>")
_SHARED_LOOKBACK_DAYS = 90
_FEED_CACHE_TTL_SECONDS = 15 * 60
_FEED_CACHE_MAXSIZE = 128


@dataclass(frozen=True)
class _FeedCacheEntry:
    expires_at: float
    start: date
    end: date
    rows: tuple[dict, ...]


_FEED_CACHE: OrderedDict[tuple[str, str, str, int], _FeedCacheEntry] = OrderedDict()
_FEED_CACHE_LOCK = RLock()
_CNINFO_FETCH_LOCK = RLock()
_RESEARCH_FETCH_LOCK = RLock()


def news_quotas() -> tuple[int, int, int]:
    """Return per-source candidate caps; the assembler owns the final total cap."""
    total = max(1, int(get_config()["news_article_limit"]))
    from ..news_selection import in_candidate_scope, source_output_limit

    if in_candidate_scope():
        return (source_output_limit(total),) * 3
    disclosure = max(1, (total + 1) // 2)
    research = max(1, total // 4)
    media = max(1, total - disclosure - research)
    return disclosure, research, media


def _request_json(method: str, url: str, *, label: str, **kwargs):
    def request():
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response.json()

    return call_with_retry(request, label=label)


@lru_cache(maxsize=1)
def _cninfo_org_ids() -> dict[str, str]:
    payload = _request_json("GET", _CNINFO_STOCKS, label="CNINFO stock directory")
    try:
        return {str(row["code"]).zfill(6): str(row["orgId"]) for row in payload["stockList"]}
    except (KeyError, TypeError) as exc:
        raise AkShareSchemaError("CNINFO stock directory changed schema.") from exc


def _parse_date(value) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _feed_cache_get(
    key: tuple[str, str, str, int], start: date, end: date
) -> list[dict] | None:
    now = time.monotonic()
    cached = _FEED_CACHE.get(key)
    if cached is None:
        return None
    if now >= cached.expires_at:
        del _FEED_CACHE[key]
        return None
    if cached.start > start or cached.end < end:
        return None
    _FEED_CACHE.move_to_end(key)
    return [dict(row) for row in cached.rows]


def _feed_cache_put(
    key: tuple[str, str, str, int], start: date, end: date, rows: list[dict]
) -> None:
    _FEED_CACHE[key] = _FeedCacheEntry(
        expires_at=time.monotonic() + _FEED_CACHE_TTL_SECONDS,
        start=start,
        end=end,
        rows=tuple(dict(row) for row in rows),
    )
    _FEED_CACHE.move_to_end(key)
    while len(_FEED_CACHE) > _FEED_CACHE_MAXSIZE:
        _FEED_CACHE.popitem(last=False)


def _clear_feed_cache() -> None:
    """Clear low-frequency response caches for deterministic tests."""
    with _FEED_CACHE_LOCK:
        _FEED_CACHE.clear()


def _shared_fetch_start(start: date, end: date) -> date:
    """Expand short same-cutoff requests to the shared 90-date candidate window."""
    return min(start, end - timedelta(days=_SHARED_LOOKBACK_DAYS - 1))


def _slice_rows(rows: list[dict], start: date, end: date) -> list[dict]:
    return [
        dict(row)
        for row in rows
        if (published := row.get("published")) is not None
        and start <= (published.date() if isinstance(published, datetime) else published) <= end
    ]


def _response_records(payload, field: str, label: str) -> list[dict]:
    """Validate a tabular JSON envelope while accepting an explicit null as empty."""
    if not isinstance(payload, dict) or field not in payload:
        raise AkShareSchemaError(f"{label} response is missing the {field!r} field.")
    records = payload[field]
    if records is None:
        return []
    if not isinstance(records, list):
        raise AkShareSchemaError(f"{label} response field {field!r} is not a list.")
    return records


def disclosure_rows(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Return exact-code CNINFO announcements in the inclusive Shanghai window."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    fetch_start = _shared_fetch_start(start, end)
    page_size = max(1, min(int(get_config().get("cn_news_candidate_limit", 100)), 100))
    cache_key = ("cninfo", code, end.isoformat(), page_size)
    with _CNINFO_FETCH_LOCK:
        with _FEED_CACHE_LOCK:
            rows = _feed_cache_get(cache_key, fetch_start, end)
        if rows is None:
            org_id = _cninfo_org_ids().get(code)
            if not org_id:
                rows = []
            else:
                payload = {
                    "pageNum": 1,
                    "pageSize": page_size,
                    "column": "szse",
                    "tabName": "fulltext",
                    "plate": "",
                    "stock": f"{code},{org_id}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": f"{fetch_start.isoformat()}~{end.isoformat()}",
                    "sortName": "time",
                    "sortType": "desc",
                    "isHLtitle": "true",
                }
                result = _request_json(
                    "POST", _CNINFO_QUERY, label="CNINFO company announcements", data=payload
                )
                # CNINFO uses ``announcements: null`` for a normal empty window.
                records = _response_records(result, "announcements", "CNINFO announcements")
                rows = []
                for record in records:
                    if (
                        not isinstance(record, dict)
                        or str(record.get("secCode", "")).zfill(6) != code
                    ):
                        continue
                    timestamp = pd.to_datetime(
                        record.get("announcementTime"), unit="ms", utc=True, errors="coerce"
                    )
                    if pd.isna(timestamp):
                        continue
                    local = timestamp.tz_convert("Asia/Shanghai")
                    if not fetch_start <= local.date() <= end:
                        continue
                    title = html.unescape(
                        _TITLE_TAG.sub("", str(record.get("announcementTitle") or ""))
                    ).strip()
                    announcement_id = str(record.get("announcementId") or "")
                    row_org = str(record.get("orgId") or org_id)
                    if not title or not announcement_id:
                        continue
                    query = urlencode(
                        {
                            "stockCode": code,
                            "announcementId": announcement_id,
                            "orgId": row_org,
                            "announcementTime": local.strftime("%Y-%m-%d"),
                        }
                    )
                    rows.append(
                        {
                            "code": code,
                            "name": str(record.get("secName") or ""),
                            "title": title,
                            "published": local.to_pydatetime(),
                            "url": f"{_CNINFO_DETAIL}?{query}",
                        }
                    )
            with _FEED_CACHE_LOCK:
                _feed_cache_put(cache_key, fetch_start, end, rows)
    return _slice_rows(rows, start, end)


def research_rows(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Return exact-code Eastmoney research reports in the inclusive window."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    fetch_start = _shared_fetch_start(start, end)
    page_size = max(1, min(int(get_config().get("cn_news_candidate_limit", 100)), 100))
    cache_key = ("eastmoney-research", code, end.isoformat(), page_size)
    with _RESEARCH_FETCH_LOCK:
        with _FEED_CACHE_LOCK:
            rows = _feed_cache_get(cache_key, fetch_start, end)
        if rows is None:
            params = {
                "industryCode": "*",
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": fetch_start.isoformat(),
                "endTime": end.isoformat(),
                "pageNo": 1,
                "pageSize": page_size,
                "qType": 0,
                "orgCode": "",
                "code": code,
                "rcode": "",
            }
            payload = _request_json(
                "GET", _RESEARCH_QUERY, label="Eastmoney stock research reports", params=params
            )
            records = _response_records(payload, "data", "Eastmoney research")
            rows = []
            for record in records:
                if (
                    not isinstance(record, dict)
                    or str(record.get("stockCode", "")).zfill(6) != code
                ):
                    continue
                published = _parse_date(record.get("publishDate"))
                if published is None or not fetch_start <= published <= end:
                    continue
                title = str(record.get("title") or "").strip()
                info_code = str(record.get("infoCode") or "")
                if not title:
                    continue
                rating = str(record.get("emRatingName") or "n/a")
                previous_rating = str(record.get("lastEmRatingName") or "").strip()
                if not previous_rating:
                    rating_change = "initiated / prior rating unavailable"
                elif rating == previous_rating:
                    rating_change = f"reiterated {rating}"
                else:
                    rating_change = f"{previous_rating} -> {rating}"
                rows.append(
                    {
                        "code": code,
                        "name": str(record.get("stockName") or ""),
                        "title": title,
                        "published": published,
                        "institution": str(record.get("orgSName") or "Unknown"),
                        "rating": rating,
                        "rating_change": rating_change,
                        "target_low": record.get("indvAimPriceL"),
                        "target_high": record.get("indvAimPriceT"),
                        "url": (
                            f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
                            if info_code
                            else ""
                        ),
                    }
                )
            with _FEED_CACHE_LOCK:
                _feed_cache_put(cache_key, fetch_start, end, rows)
    return _slice_rows(rows, start, end)


def _dedupe_limit(rows: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    seen: set[str] = set()
    kept = []
    for row in sorted(rows, key=lambda item: item["published"], reverse=True):
        key = canonical_headline(row["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(row)
        if len(kept) >= limit:
            break
    return kept


def get_disclosure_news(ticker: str, start_date: str, end_date: str) -> str:
    disclosure_limit, _research_limit, _media_limit = news_quotas()
    rows = _dedupe_limit(
        disclosure_rows(ticker, start_date, end_date),
        disclosure_limit,
    )
    if not rows:
        return f"No CNINFO announcements found for {ticker} between {start_date} and {end_date}"
    body = "\n\n".join(
        f"### [direct] {row['title']}\nDisclosed: {row['published'].strftime('%Y-%m-%d %H:%M')} CST · Link: {row['url']}"
        for row in rows
    )
    return f"## {ticker} company announcements (CNINFO), from {start_date} to {end_date}:\n\n{body}"


def get_research_news(ticker: str, start_date: str, end_date: str) -> str:
    _disclosure_limit, research_limit, _media_limit = news_quotas()
    rows = _dedupe_limit(
        research_rows(ticker, start_date, end_date),
        research_limit,
    )
    if not rows:
        return (
            f"No Eastmoney research reports found for {ticker} between {start_date} and {end_date}"
        )
    body = "\n\n".join(
        f"### [direct] {row['title']} (institution: {row['institution']})\nPublished: {row['published']} CST · Rating: {row['rating']} · PDF: {row['url'] or 'n/a'}"
        for row in rows
    )
    return f"## {ticker} sell-side research (Eastmoney), from {start_date} to {end_date}:\n\n{body}"
