"""Bounded Sina finance-report transport and conservative visibility filtering."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ..errors import NoMarketDataError
from .common import (
    REQUEST_TIMEOUT,
    AkShareSchemaError,
    call_with_retry,
    canonical_a_share,
    load_akshare,
)

_REPORT_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
_SOURCE_BY_KIND = {
    "abstract": "gjzb",
    "balance": "fzb",
    "income": "lrb",
    "cashflow": "llb",
}
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _date_value(value) -> pd.Timestamp | pd.NaT:
    """Parse Sina date strings or epoch seconds/milliseconds conservatively."""
    if value is None or value == "" or pd.isna(value):
        return pd.NaT
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) in {10, 13} and stripped.isdigit():
            value = int(stripped)
    if isinstance(value, (int, float)) and not pd.isna(value):
        unit = "ms" if abs(value) >= 10**12 else "s"
        parsed = pd.to_datetime(value, unit=unit, errors="coerce", utc=True)
        if pd.isna(parsed):
            return pd.NaT
        return parsed.tz_convert(_SHANGHAI_TZ).tz_localize(None).normalize()
    parsed = pd.to_datetime(value, errors="coerce")
    return pd.NaT if pd.isna(parsed) else parsed.normalize()


def validate_analysis_date(curr_date: str | None) -> None:
    """Reject malformed analysis dates before any vendor request is attempted."""
    if curr_date is None:
        return
    try:
        parsed = date.fromisoformat(curr_date)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid analysis date {curr_date!r}; expected YYYY-MM-DD."
        ) from exc
    if parsed.isoformat() != curr_date:
        raise ValueError(f"Invalid analysis date {curr_date!r}; expected YYYY-MM-DD.")


def _fetch_payload(stock: str, source: str) -> dict:
    # Keep AkShare as the declared runtime dependency/vendor while bypassing its
    # unbounded requests.get call so this adapter can enforce a finite timeout.
    load_akshare()

    def request_report():
        response = requests.get(
            _REPORT_URL,
            params={
                "paperCode": stock,
                "source": source,
                "type": "0",
                "page": "1",
                "num": "1000",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    return call_with_retry(
        request_report,
        label=f"AkShare/Sina finance report ({source})",
    )


def _records_from_payload(payload: dict) -> pd.DataFrame:
    try:
        data = payload["result"]["data"]
        report_dates = [item["date_value"] for item in data["report_date"]]
        report_list = data["report_list"]
    except (KeyError, TypeError) as exc:
        raise AkShareSchemaError(f"Sina finance-report response changed envelope: {exc}") from exc

    rows: list[dict] = []
    for report_date in report_dates:
        report = report_list.get(report_date)
        if not isinstance(report, dict):
            raise AkShareSchemaError(
                f"Sina finance-report response is missing period {report_date!r}."
            )
        items = report.get("data")
        if not isinstance(items, list):
            raise AkShareSchemaError(
                f"Sina finance-report period {report_date!r} has no item list."
            )
        row: dict[str, object] = {
            "ReportDate": _date_value(report_date),
            "PublishDate": _date_value(report.get("publish_date")),
            "UpdateDate": _date_value(report.get("update_time")),
            "DataSource": report.get("data_source"),
            "Audited": report.get("is_audit"),
            "Currency": report.get("rCurrency"),
            "StatementType": report.get("rType"),
        }
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("item_title") or "").strip()
            if title and title not in row:
                row[title] = pd.to_numeric(item.get("item_value"), errors="coerce")
        visible = [row[key] for key in ("ReportDate", "PublishDate", "UpdateDate")]
        visible = [value for value in visible if not pd.isna(value)]
        row["VisibilityDate"] = max(visible) if visible else pd.NaT
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_finance_records(symbol: str, kind: str) -> tuple[str, pd.DataFrame]:
    """Fetch normalized Sina rows for ``abstract`` or one statement kind."""
    if kind not in _SOURCE_BY_KIND:
        raise ValueError(f"Unsupported Sina finance-report kind {kind!r}.")
    canonical, code, exchange = canonical_a_share(symbol)
    payload = _fetch_payload(f"{exchange}{code}", _SOURCE_BY_KIND[kind])
    frame = _records_from_payload(payload)
    if frame.empty:
        raise NoMarketDataError(symbol, canonical, f"Sina returned no {kind} report periods")
    return canonical, frame


def filter_visible_records(
    frame: pd.DataFrame,
    curr_date: str | None,
    freq: str = "quarterly",
    *,
    limit: int = 8,
) -> pd.DataFrame:
    """Filter by max(report, publication, update) date and dedupe revisions."""
    validate_analysis_date(curr_date)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=getattr(frame, "columns", None))
    required = {"ReportDate", "VisibilityDate"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise AkShareSchemaError(
            f"Normalized Sina report frame is missing required columns {missing}."
        )
    result = frame.copy()
    for column in ("ReportDate", "PublishDate", "UpdateDate", "VisibilityDate"):
        if column in result.columns:
            result[column] = result[column].map(_date_value)
    if curr_date is not None:
        cutoff = _date_value(curr_date)
        result = result[result["VisibilityDate"].notna() & (result["VisibilityDate"] <= cutoff)]
    if freq == "annual":
        result = result[
            result["ReportDate"].notna()
            & (result["ReportDate"].dt.month == 12)
            & (result["ReportDate"].dt.day == 31)
        ]
    result = result.sort_values(
        ["ReportDate", "VisibilityDate"], ascending=[False, False]
    ).drop_duplicates("ReportDate", keep="first")
    return result.head(max(1, int(limit))).reset_index(drop=True)


def retrieval_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
