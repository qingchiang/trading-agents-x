"""Bounded, date-safe A-share per-name sentiment signals."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import datetime, timedelta
from io import BytesIO
from threading import Lock

import pandas as pd
import requests

from tradingagents.application.evidence_workset import StructuredNumericFact
from tradingagents.dataflows.measurement import instrument_currency
from tradingagents.provenance import ProvenanceRecord, attach_provenance
from tradingagents.version import BROWSER_USER_AGENT

from .calendar import previous_trade_date
from .common import (
    REQUEST_TIMEOUT,
    AkShareRequestError,
    AkShareSchemaError,
    call_with_retry,
    canonical_a_share,
)
from .news_sources import disclosure_rows, research_rows
from .sina_ratings import rating_rows as sina_rating_rows

_SSE_MARGIN = "https://query.sse.com.cn/marketdata/tradedata/queryMargin.do"
_SZSE_REPORT = "https://www.szse.cn/api/report/ShowReport"
_EASTMONEY_DATA = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_UA = BROWSER_USER_AGENT
_HOLDING_PAGE_SIZE = 100
_HOLDING_CACHE_TTL_SECONDS = 15 * 60
_HOLDING_CACHE_MAXSIZE = 128
_HOLDING_CACHE: OrderedDict[
    tuple[str, str, str, str], tuple[float, tuple[dict, ...], bool]
] = OrderedDict()
_HOLDING_CACHE_LOCK = Lock()


def _request(method: str, url: str, *, label: str, **kwargs):
    def request():
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        return response

    return call_with_retry(request, label=label)


def _amount(value) -> str:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    parsed = pd.to_numeric(value, errors="coerce")
    return "n/a" if pd.isna(parsed) else f"{float(parsed):,.0f}"


def _display(value) -> str:
    return "n/a" if value is None or pd.isna(value) or value == "" else str(value)


def _first_present(row, *keys: str):
    """Return the first non-null field without discarding numeric zero."""
    for key in keys:
        value = row.get(key)
        if value is not None and not pd.isna(value):
            return value
    return None


def get_margin_signal(ticker: str, curr_date: str, _remaining_sessions: int = 5) -> str:
    """Return latest on/before-date official exchange margin detail."""
    _canonical, code, exchange = canonical_a_share(ticker)
    trade_date = previous_trade_date(curr_date)
    compact = trade_date.strftime("%Y%m%d")
    headers = {"User-Agent": _UA}
    if exchange == "sh":
        headers["Referer"] = "https://www.sse.com.cn/"
        response = _request(
            "GET",
            _SSE_MARGIN,
            label="SSE margin detail",
            params={
                "isPagination": "true",
                "tabType": "mxtype",
                "detailsDate": compact,
                "stockCode": code,
                "pageHelp.pageSize": 20,
                "pageHelp.pageNo": 1,
            },
            headers=headers,
        )
        payload = response.json()
        if not isinstance(payload, dict) or "result" not in payload:
            raise AkShareSchemaError("SSE margin detail response is missing result.")
        rows = payload["result"]
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise AkShareSchemaError("SSE margin detail result is not a list.")
        code_keys = ("SECURITY_CODE", "securityCode", "stockCode")
        row = next(
            (
                item
                for item in rows
                if isinstance(item, dict)
                and str(_first_present(item, *code_keys) or "") == code
            ),
            None,
        )
        if row is None and rows and not any(
            isinstance(item, dict) and _first_present(item, *code_keys) is not None
            for item in rows
        ):
            raise AkShareSchemaError(
                "SSE margin detail rows do not expose a recognized security-code field."
            )
        if not isinstance(row, dict):
            return _earlier_margin_or_uncovered(
                ticker, trade_date, "SSE", code, _remaining_sessions
            )
        financing = _first_present(row, "FIN_BALANCE", "RZYE", "rzye", "finBalance")
        buy = _first_present(row, "FIN_BUY_AMT", "RZMRE", "rzmre", "finBuyAmount")
        short = _first_present(
            row, "SEC_LENDING_BALANCE", "RQYL", "rqyl", "securityBalance"
        )
        if financing is None and buy is None and short is None:
            raise AkShareSchemaError(
                "SSE margin detail row has no recognized financing fields."
            )
    else:
        headers["Referer"] = "https://www.szse.cn/disclosure/margin/margin/index.html"
        response = _request(
            "GET",
            _SZSE_REPORT,
            label="SZSE margin detail",
            params={
                "SHOWTYPE": "xlsx",
                "CATALOGID": "1837_xxpl",
                "txtDate": trade_date.strftime("%Y-%m-%d"),
                "TABKEY": "tab2",
                "tab2PAGENO": 1,
            },
            headers=headers,
        )
        try:
            frame = pd.read_excel(BytesIO(response.content), dtype={"证券代码": str})
        except Exception as exc:  # noqa: BLE001 - parser engine errors vary
            raise AkShareSchemaError("SZSE margin workbook could not be parsed.") from exc
        if "证券代码" not in frame.columns:
            raise AkShareSchemaError("SZSE margin workbook is missing 证券代码.")
        matches = frame[frame["证券代码"].astype(str).str.zfill(6) == code]
        if matches.empty:
            return _earlier_margin_or_uncovered(
                ticker, trade_date, "SZSE", code, _remaining_sessions
            )
        metric_columns = {
            "融资余额",
            "融资余额(元)",
            "融资买入额",
            "融资买入额(元)",
            "融券余量",
            "融券余量(股/份)",
        }
        if not metric_columns.intersection(frame.columns):
            raise AkShareSchemaError(
                "SZSE margin workbook has no recognized financing columns."
            )
        row = matches.iloc[0]
        financing = row.get("融资余额", row.get("融资余额(元)"))
        buy = row.get("融资买入额", row.get("融资买入额(元)"))
        short = row.get("融券余量", row.get("融券余量(股/份)"))
    return (
        f"Official margin detail for {code} on {trade_date}: financing balance="
        f"{_amount(financing)} CNY; financing buys={_amount(buy)} CNY; "
        f"securities-lending balance={_amount(short)} shares. Missing fields are n/a."
    )


def _earlier_margin_or_uncovered(
    ticker: str,
    trade_date,
    exchange_name: str,
    code: str,
    remaining_sessions: int,
) -> str:
    """Walk through publication lag, then distinguish sustained non-coverage."""
    if remaining_sessions > 1:
        earlier = previous_trade_date(trade_date, inclusive=False)
        return get_margin_signal(ticker, earlier.isoformat(), remaining_sessions - 1)
    return (
        f"<{exchange_name} margin detail: no covered row for {code} in the checked "
        f"exchange sessions ending {trade_date}>"
    )


def get_holding_changes(ticker: str, curr_date: str) -> str:
    """Return major-shareholder/executive changes bounded by available dates."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    start = end - timedelta(days=89)
    feed_specs = (
        (
            "major-shareholder",
            "RPT_SHARE_HOLDER_INCREASE",
            "NOTICE_DATE",
            "NOTICE_DATE",
            "-1",
            _major_holder_events,
        ),
        (
            "executive",
            "RPT_EXECUTIVE_HOLD_DETAILS",
            "CHANGE_DATE",
            "CHANGE_DATE,SECURITY_CODE,PERSON_NAME",
            "-1,1,1",
            _executive_events,
        ),
    )
    events = []
    failures: list[tuple[str, Exception]] = []
    coverage_notes: list[str] = []
    provenance: list[ProvenanceRecord] = []
    successful_sources = 0
    for source, report_name, date_field, sort_columns, sort_types, parse in feed_specs:
        try:
            records, truncated = _eastmoney_holding_records(
                code,
                report_name,
                date_field,
                sort_columns,
                sort_types,
                start,
                end,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the other independent feed
            failures.append((source, exc))
            provenance.append(
                ProvenanceRecord(
                    evidence=f"{source} holding changes",
                    source=f"Eastmoney {source} disclosures",
                    requested=f"{start} to {end}",
                    effective="—",
                    timing="unavailable",
                )
            )
            continue
        successful_sources += 1
        timing = "event/disclosure/update-date filtered"
        if truncated:
            coverage_notes.append(
                f"<{source} holding feed truncated: latest {_HOLDING_PAGE_SIZE} "
                "window records used; coverage is incomplete>"
            )
            timing += "; partial coverage; result set truncated"
        parsed = parse(records, start, end)
        if any("non-strict PIT" in text for _visible, text in parsed):
            timing += "; non-strict PIT"
        if not parsed:
            timing = f"available; no qualifying records; {timing}"
        provenance.append(
            ProvenanceRecord(
                evidence=f"{source} holding changes",
                source=f"Eastmoney {source} disclosures",
                requested=f"{start} to {end}",
                effective=f"{start} to {end}",
                timing=timing,
            )
        )
        events.extend(parsed)

    notes = [
        f"<{source} holding feed unavailable: {type(exc).__name__}>"
        for source, exc in failures
    ]
    notes.extend(coverage_notes)
    cninfo_succeeded = False
    if failures:
        try:
            announcements = disclosure_rows(ticker, start.isoformat(), end.isoformat())
        except Exception as exc:  # noqa: BLE001 - preserve structured feed results
            notes.append(f"<CNINFO holding-announcement fallback unavailable: {type(exc).__name__}>")
            provenance.append(
                ProvenanceRecord(
                    evidence="holding-change announcement fallback",
                    source="CNINFO",
                    requested=f"{start} to {end}",
                    effective="—",
                    timing="unavailable",
                )
            )
        else:
            cninfo_succeeded = True
            matched = [
                row
                for row in announcements
                if any(term in row["title"] for term in _HOLDING_ANNOUNCEMENT_TERMS)
            ]
            for row in matched:
                visible = row["published"].date()
                events.append(
                    (
                        visible,
                        f"- {visible}: [official announcement fallback] {row['title']}; "
                        "timing=disclosure-date filtered",
                    )
                )
            provenance.append(
                ProvenanceRecord(
                    evidence="holding-change announcement fallback",
                    source="CNINFO",
                    requested=f"{start} to {end}",
                    effective=f"{start} to {end}",
                    timing=(
                        "fallback source used; disclosure-date filtered"
                        if matched
                        else "available; no qualifying records; queried after structured "
                        "holding-feed failure"
                    ),
                )
            )

    if successful_sources == 0 and not cninfo_succeeded:
        raise failures[0][1]
    if not events:
        source_label = "Eastmoney" if not failures else "available"
        empty = (
            f"<{source_label} holding changes: no matching events in available feeds "
            f"for {code} from {start} to {end}>"
        )
        return attach_provenance("\n".join((empty, *notes)), *provenance)
    lines = [
        text for _visible, text in sorted(events, key=lambda item: item[0], reverse=True)[:8]
    ]
    if notes:
        lines.extend(notes)
    return attach_provenance(
        "Major-shareholder/executive holding changes "
        "(disclosure/update-date filtered where available; records without those dates "
        "use event dates and are non-strict PIT):\n"
        + "\n".join(lines),
        *provenance,
    )


def _eastmoney_holding_records(
    code: str,
    report_name: str,
    date_field: str,
    sort_columns: str,
    sort_types: str,
    start,
    end,
) -> tuple[list[dict], bool]:
    """Fetch one server-filtered page; return records and truncation state."""
    cache_key = (code, report_name, start.isoformat(), end.isoformat())
    cached = _holding_cache_get(cache_key)
    if cached is not None:
        return cached

    date_filter = (
        f'(SECURITY_CODE="{code}")'
        f"({date_field}>='{start.isoformat()}')"
        f"({date_field}<='{end.isoformat()}')"
    )
    response = _request(
        "GET",
        _EASTMONEY_DATA,
        label=f"Eastmoney {report_name}",
        params={
            "reportName": report_name,
            "columns": "ALL",
            "filter": date_filter,
            "pageNumber": 1,
            "pageSize": _HOLDING_PAGE_SIZE,
            "sortTypes": sort_types,
            "sortColumns": sort_columns,
            "source": "WEB",
            "client": "WEB",
        },
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise AkShareSchemaError(f"Eastmoney {report_name} returned an invalid envelope.")
    if payload.get("success") is False:
        if str(payload.get("code") or "") == "9201":
            _holding_cache_put(cache_key, [], False)
            return [], False
        raise AkShareRequestError(
            f"Eastmoney {report_name} rejected the request: "
            f"{payload.get('message') or 'unknown error'}"
        )
    if "result" not in payload or not isinstance(payload["result"], dict):
        raise AkShareSchemaError(f"Eastmoney {report_name} response has no result object.")
    result = payload["result"]
    if "data" not in result:
        raise AkShareSchemaError(f"Eastmoney {report_name} result is missing data.")
    records = result["data"]
    if records is None:
        records = []
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise AkShareSchemaError(f"Eastmoney {report_name} returned invalid rows.")
    if records and not all(str(row.get("SECURITY_CODE") or "").zfill(6) == code for row in records):
        raise AkShareSchemaError(
            f"Eastmoney {report_name} returned rows for an unexpected security code."
        )
    raw_pages = result.get("pages")
    try:
        total_pages = int(raw_pages) if raw_pages not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise AkShareSchemaError(
            f"Eastmoney {report_name} returned an invalid page count."
        ) from exc
    truncated = (total_pages is not None and total_pages > 1) or (
        total_pages is None and len(records) >= _HOLDING_PAGE_SIZE
    )
    _holding_cache_put(cache_key, records, truncated)
    return [dict(row) for row in records], truncated


def _holding_cache_get(
    key: tuple[str, str, str, str],
) -> tuple[list[dict], bool] | None:
    now = time.monotonic()
    with _HOLDING_CACHE_LOCK:
        cached = _HOLDING_CACHE.get(key)
        if cached is None:
            return None
        expires_at, records, truncated = cached
        if now >= expires_at:
            del _HOLDING_CACHE[key]
            return None
        _HOLDING_CACHE.move_to_end(key)
    return [dict(row) for row in records], truncated


def _holding_cache_put(
    key: tuple[str, str, str, str], records: list[dict], truncated: bool
) -> None:
    cached_records = tuple(dict(row) for row in records)
    with _HOLDING_CACHE_LOCK:
        _HOLDING_CACHE[key] = (
            time.monotonic() + _HOLDING_CACHE_TTL_SECONDS,
            cached_records,
            truncated,
        )
        _HOLDING_CACHE.move_to_end(key)
        while len(_HOLDING_CACHE) > _HOLDING_CACHE_MAXSIZE:
            _HOLDING_CACHE.popitem(last=False)


def _clear_holding_cache() -> None:
    """Clear process-local holding history cache for deterministic tests."""
    with _HOLDING_CACHE_LOCK:
        _HOLDING_CACHE.clear()


def _visible_date(row: dict, event_field: str):
    """Use the later event/disclosure/update timestamp conservatively."""
    values = [
        pd.to_datetime(row.get(field), errors="coerce")
        for field in (event_field, "NOTICE_DATE", "EITIME")
        if row.get(field) not in (None, "")
    ]
    dated = [value for value in values if not pd.isna(value)]
    return max(dated).date() if dated else None


def _visibility_timing(row: dict) -> str:
    """Describe whether a record has a disclosure/update visibility boundary."""
    has_visibility_date = any(
        row.get(field) not in (None, "")
        and not pd.isna(pd.to_datetime(row.get(field), errors="coerce"))
        for field in ("NOTICE_DATE", "EITIME")
    )
    return (
        "disclosure/update-date filtered"
        if has_visibility_date
        else "event-date only; non-strict PIT"
    )


def _major_holder_events(records: list[dict], start, end) -> list[tuple]:
    events = []
    for row in records:
        visible = _visible_date(row, "END_DATE")
        if visible is None or not start <= visible <= end:
            continue
        direction = row.get("DIRECTION") or row.get("HOLD_CHANGE") or "n/a"
        holder = row.get("HOLDER_NAME") or row.get("HOLDER_NAME_NEW") or "unknown holder"
        shares = row.get("CHANGE_SHARES")
        if shares is None:
            change_in_ten_thousands = pd.to_numeric(row.get("CHANGE_NUM"), errors="coerce")
            shares = (
                None
                if pd.isna(change_in_ten_thousands)
                else float(change_in_ten_thousands) * 10_000
            )
        events.append(
            (
                visible,
                f"- {visible}: [major shareholder] {holder}; {direction}; "
                f"shares={_amount(shares)}; timing={_visibility_timing(row)}",
            )
        )
    return events


def _executive_events(records: list[dict], start, end) -> list[tuple]:
    events = []
    for row in records:
        visible = _visible_date(row, "CHANGE_DATE")
        if visible is None or not start <= visible <= end:
            continue
        shares = pd.to_numeric(row.get("CHANGE_SHARES"), errors="coerce")
        direction = "n/a"
        if not pd.isna(shares):
            direction = "增持" if shares > 0 else "减持" if shares < 0 else "持股未变"
        person = row.get("PERSON_NAME") or row.get("DSE_PERSON_NAME") or "unknown person"
        role = row.get("POSITION_NAME") or row.get("PERSON_DSE_RELATION") or "role n/a"
        events.append(
            (
                visible,
                f"- {visible}: [executive] {person} ({role}); {direction}; "
                f"shares={_amount(abs(shares) if not pd.isna(shares) else None)}; "
                f"timing={_visibility_timing(row)}",
            )
        )
    return events


def get_research_signal_payload(
    ticker: str,
    curr_date: str,
) -> tuple[str, tuple[StructuredNumericFact, ...]]:
    """Return dated sell-side prose plus exact target-price facts."""
    end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    start = end - timedelta(days=89)
    requested = f"{start} to {end}"
    provenance = []
    notes = []
    try:
        rows = sina_rating_rows(ticker, start.isoformat(), end.isoformat())
    except Exception as exc:  # noqa: BLE001 - Eastmoney remains an independent fallback
        rows = []
        notes.append(f"<Sina rating feed unavailable: {type(exc).__name__}>")
        provenance.append(
            ProvenanceRecord(
                evidence="sell-side ratings and target prices",
                source="Sina Finance institutional ratings",
                requested=requested,
                effective="—",
                timing="unavailable",
            )
        )
        fallback_reason = "Sina rating feed unavailable"
    else:
        provenance.append(
            ProvenanceRecord(
                evidence="sell-side ratings and target prices",
                source="Sina Finance institutional ratings",
                requested=requested,
                effective=requested,
                timing=(
                    "publication-date filtered"
                    if rows
                    else "available; no relevant items in window; returned_items=0"
                ),
            )
        )
        fallback_reason = "Sina returned no qualifying ratings in the requested window"

    source = "Sina Finance"
    if not rows:
        try:
            rows = research_rows(ticker, start.isoformat(), end.isoformat())
        except Exception as exc:  # noqa: BLE001 - keep Sina's successful-empty state visible
            notes.append(f"<Eastmoney research fallback unavailable: {type(exc).__name__}>")
            provenance.append(
                ProvenanceRecord(
                    evidence="sell-side ratings and target prices",
                    source="Eastmoney Research",
                    requested=requested,
                    effective="—",
                    timing="unavailable",
                )
            )
            rows = []
        else:
            provenance.append(
                ProvenanceRecord(
                    evidence="sell-side ratings and target prices",
                    source="Eastmoney Research",
                    requested=requested,
                    effective=requested,
                    timing=(
                        f"fallback source used; reason={fallback_reason}; publication-date filtered"
                        if rows
                        else "available; no relevant items in window; returned_items=0; "
                        "queried after Sina returned no usable rows"
                    ),
                )
            )
            source = "Eastmoney Research"

    if not rows:
        body = f"<Sina/Eastmoney research: no usable coverage in {start} to {end}>"
        if notes:
            body += "\n" + "\n".join(notes)
        return attach_provenance(body, *provenance), ()
    lines = []
    facts: list[StructuredNumericFact] = []
    currency = instrument_currency(ticker)
    selected = sorted(rows, key=lambda item: item["published"], reverse=True)[:8]
    for index, row in enumerate(selected, start=1):
        target = f"{_display(row['target_low'])}–{_display(row['target_high'])}"
        detail = (
            f"- {row['published']}: {row['institution']}; rating={row['rating']}; "
            f"rating_change={row['rating_change']}; target={target}"
        )
        if row.get("analyst") and row["analyst"].casefold() != "nan":
            detail += f"; analysts={row['analyst']}"
        if row.get("title"):
            detail += f"; {row['title']}"
        lines.append(detail)
        for bound in ("low", "high"):
            parsed = pd.to_numeric(row.get(f"target_{bound}"), errors="coerce")
            if pd.isna(parsed):
                continue
            facts.append(
                StructuredNumericFact(
                    key=f"target_{bound}_{index}",
                    label=f"{row['institution']} target {bound}",
                    value=float(parsed),
                    measurement_kind="currency",
                    unit=currency,
                    effective_date=str(row["published"]),
                )
            )
    if notes:
        lines.extend(notes)
    return attach_provenance(
        f"Sell-side rating and target changes ({source}; publication-date filtered):\n"
        + "\n".join(lines),
        *provenance,
    ), tuple(facts)


def get_research_signal(ticker: str, curr_date: str) -> str:
    """Return recent ratings and target ranges known by the analysis date."""

    return get_research_signal_payload(ticker, curr_date)[0]


_IMPORTANT_TERMS = (
    "业绩",
    "利润",
    "亏损",
    "风险提示",
    "重大",
    "收购",
    "重组",
    "回购",
    "增持",
    "减持",
    "分红",
    "诉讼",
    "处罚",
    "停牌",
    "复牌",
    "退市",
)

_HOLDING_ANNOUNCEMENT_TERMS = (
    "增持",
    "减持",
    "持股变动",
    "权益变动",
)


def get_important_announcements(ticker: str, curr_date: str) -> str:
    """Return directly code-bound, potentially material CNINFO announcements."""
    end = datetime.strptime(curr_date, "%Y-%m-%d").date()
    start = end - timedelta(days=29)
    rows = disclosure_rows(ticker, start.isoformat(), end.isoformat())
    rows = [row for row in rows if any(term in row["title"] for term in _IMPORTANT_TERMS)]
    if not rows:
        return f"<CNINFO important announcements: no matched events in {start} to {end}>"
    rows.sort(key=lambda row: row["published"], reverse=True)
    return "Important company announcements (exact-code, disclosure-date filtered):\n" + "\n".join(
        f"- {row['published'].strftime('%Y-%m-%d %H:%M')} CST: {row['title']}" for row in rows[:10]
    )
