"""Bounded A-share disclosure and sell-side research feeds."""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from functools import lru_cache
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


def news_quotas() -> tuple[int, int, int]:
    """Return per-source candidate caps; the assembler owns the final total cap."""
    total = max(1, int(get_config()["news_article_limit"]))
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
    org_id = _cninfo_org_ids().get(code)
    if not org_id:
        return []
    payload = {
        "pageNum": 1,
        "pageSize": min(max(get_config()["news_article_limit"] * 4, 30), 100),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "time",
        "sortType": "desc",
        "isHLtitle": "true",
    }
    result = _request_json(
        "POST", _CNINFO_QUERY, label="CNINFO company announcements", data=payload
    )
    # CNINFO uses ``announcements: null`` for a normal empty window. Treat it as
    # empty before inspecting columns, avoiding AkShare's historical KeyError.
    records = _response_records(result, "announcements", "CNINFO announcements")
    rows = []
    for record in records:
        if not isinstance(record, dict) or str(record.get("secCode", "")).zfill(6) != code:
            continue
        timestamp = pd.to_datetime(
            record.get("announcementTime"), unit="ms", utc=True, errors="coerce"
        )
        if pd.isna(timestamp):
            continue
        local = timestamp.tz_convert("Asia/Shanghai")
        if not start <= local.date() <= end:
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
    return rows


def research_rows(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Return exact-code Eastmoney research reports in the inclusive window."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    params = {
        "industryCode": "*",
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": start_date,
        "endTime": end_date,
        "pageNo": 1,
        "pageSize": min(max(get_config()["news_article_limit"] * 4, 30), 100),
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
        if not isinstance(record, dict) or str(record.get("stockCode", "")).zfill(6) != code:
            continue
        published = _parse_date(record.get("publishDate"))
        if published is None or not start <= published <= end:
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
                "url": f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf" if info_code else "",
            }
        )
    return rows


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
