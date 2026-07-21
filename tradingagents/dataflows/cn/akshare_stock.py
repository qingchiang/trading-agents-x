"""AkShare qfq daily OHLCV with Tencent→Eastmoney internal fallback."""

from __future__ import annotations

import logging
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from tradingagents.provenance import ProvenanceRecord, attach_provenance

from ..symbol_utils import NoMarketDataError
from .calendar import effective_trade_date
from .common import (
    REQUEST_TIMEOUT,
    AkShareRateLimitError,
    AkShareRequestError,
    AkShareSchemaError,
    AkShareUnavailableError,
    call_with_retry,
    canonical_a_share,
    load_akshare,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)
_CACHE_MAX_ENTRIES = 128
_FRAME_CACHE: OrderedDict[tuple[str, str, str], OHLCVResult] = OrderedDict()
_TENCENT_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
_TENCENT_PAGE_SIZE = 640
_TENCENT_MAX_PAGES = 128
_LOG_DETAIL_MAX_CHARS = 400
_LATEST_ROW_RE = re.compile(r"latest row is (\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|authorization)=([^&\s]+)"
)
ADJUSTMENT_FALLBACK_NOTE = (
    "adjustment provider changed; technical indicators may differ"
)
_LOT_BASED_VOLUME_SOURCES = frozenset(
    {"AkShare / Eastmoney", "AkShare / Tencent"}
)
_SHARES_PER_LOT = 100


@dataclass(frozen=True)
class OHLCVResult:
    frame: pd.DataFrame
    source: str
    canonical: str
    requested_end: str
    effective_end: str
    adjustment: str = "qfq (forward-adjusted)"
    fallback_reason: str | None = None


_COLUMN_ALIASES = {
    "日期": "Date",
    "date": "Date",
    "开盘": "Open",
    "open": "Open",
    "最高": "High",
    "high": "High",
    "最低": "Low",
    "low": "Low",
    "收盘": "Close",
    "close": "Close",
    "成交量": "Volume",
    "volume": "Volume",
    # Tencent names its sixth daily-bar field ``amount`` although it is the
    # traded-volume field in this endpoint.
    "amount": "Volume",
    "成交额": "Amount",
    "振幅": "AmplitudePct",
    "涨跌幅": "PctChange",
    "涨跌额": "PriceChange",
    "换手率": "TurnoverPct",
}
_REQUIRED_COLUMNS = ("Date", "Open", "High", "Low", "Close", "Volume")
_EXTENDED_COLUMNS = ("Amount", "AmplitudePct", "PctChange", "PriceChange", "TurnoverPct")


def clear_cache() -> None:
    """Clear the successful-frame LRU (primarily for tests)."""
    _FRAME_CACHE.clear()


def _health_failure(exc: Exception) -> tuple[str, str, str]:
    """Return bounded, one-line endpoint-health fields for an exception."""
    raw_detail = exc.detail if isinstance(exc, NoMarketDataError) else str(exc)
    detail = " ".join(str(raw_detail).split())
    detail = _SENSITIVE_QUERY_RE.sub(r"\1=<redacted>", detail)
    if len(detail) > _LOG_DETAIL_MAX_CHARS:
        detail = detail[: _LOG_DETAIL_MAX_CHARS - 3] + "..."

    latest_match = _LATEST_ROW_RE.search(detail)
    latest = latest_match.group(1) if latest_match else "n/a"
    if isinstance(exc, NoMarketDataError):
        status = "stale" if latest_match or "stale" in detail.casefold() else "no_data"
    elif isinstance(exc, AkShareSchemaError):
        status = "schema_error"
    elif isinstance(exc, AkShareRateLimitError):
        status = "rate_limited"
    elif isinstance(exc, AkShareUnavailableError):
        status = "unavailable"
    elif isinstance(exc, AkShareRequestError):
        status = "request_error"
    else:
        status = "unexpected_error"
    return status, latest, detail or type(exc).__name__


def _remember(key: tuple[str, str, str], result: OHLCVResult) -> None:
    _FRAME_CACHE[key] = result
    _FRAME_CACHE.move_to_end(key)
    while len(_FRAME_CACHE) > _CACHE_MAX_ENTRIES:
        _FRAME_CACHE.popitem(last=False)


def _normalize_frame(
    raw: pd.DataFrame,
    *,
    symbol: str,
    canonical: str,
    source: str,
    start_date: str,
    expected_end: str,
) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise NoMarketDataError(
            symbol, canonical, f"{source} returned no qfq rows"
        )
    frame = raw.rename(columns=_COLUMN_ALIASES).copy()
    missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise AkShareSchemaError(
            f"{source} response is missing required columns {missing}; "
            f"received {list(raw.columns)}"
        )
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    numeric = [column for column in (*_REQUIRED_COLUMNS[1:], *_EXTENDED_COLUMNS) if column in frame]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS))
    if source in _LOT_BASED_VOLUME_SOURCES:
        frame["Volume"] = frame["Volume"] * _SHARES_PER_LOT
    frame = frame[
        (frame["Date"] >= pd.Timestamp(start_date))
        & (frame["Date"] <= pd.Timestamp(expected_end))
    ]
    frame = frame.sort_values("Date").drop_duplicates("Date", keep="last")
    finite_required = frame[list(_REQUIRED_COLUMNS[1:])].apply(
        lambda column: column.map(math.isfinite)
    ).all(axis=1)
    valid_prices = (
        finite_required
        & (frame["High"] >= frame[["Open", "Close", "Low"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close", "High"]].min(axis=1))
        & (frame["Volume"] >= 0)
    )
    frame = frame[valid_prices]
    if frame.empty:
        raise NoMarketDataError(
            symbol, canonical, f"{source} returned no usable qfq rows after validation"
        )
    latest = frame["Date"].max().strftime("%Y-%m-%d")
    if latest != expected_end:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"{source} latest row is {latest}, before expected mainland trading "
            f"date {expected_end} (suspended, delisted, or stale)",
        )
    ordered = [*_REQUIRED_COLUMNS, *[c for c in _EXTENDED_COLUMNS if c in frame]]
    return frame.loc[:, ordered].reset_index(drop=True)


def _fetch_eastmoney(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    ak = load_akshare()
    return call_with_retry(
        ak.stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
        timeout=REQUEST_TIMEOUT,
        label="AkShare Eastmoney stock_zh_a_hist",
    )


def _fetch_tencent_page(
    prefixed_code: str,
    start_date: str,
    end_date: str,
    *,
    decoder,
) -> pd.DataFrame:
    """Fetch and validate one bounded Tencent qfq page."""
    params = {
        "_var": f"kline_dayqfq{end_date.replace('-', '')}",
        "param": (
            f"{prefixed_code},day,{start_date},{end_date},"
            f"{_TENCENT_PAGE_SIZE},qfq"
        ),
        "r": "0.8205512681390605",
    }

    def request_page():
        response = requests.get(
            _TENCENT_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response

    response = call_with_retry(
        request_page,
        label=f"AkShare Tencent stock_zh_a_hist_tx ({start_date} to {end_date})",
    )
    try:
        marker = response.text.find("={")
        if marker < 0:
            raise ValueError("missing JSON assignment marker")
        payload = decoder.decode(response.text[marker + 1 :])
        stock_data = payload.get("data", {}).get(prefixed_code, {})
    except Exception as exc:  # noqa: BLE001 - upstream payload failures vary
        raise AkShareSchemaError(
            f"AkShare Tencent response for {start_date} to {end_date} "
            f"could not be decoded: {exc}"
        ) from exc
    if not isinstance(stock_data, dict) or "qfqday" not in stock_data:
        keys = sorted(stock_data) if isinstance(stock_data, dict) else []
        raise AkShareSchemaError(
            "AkShare Tencent qfq response is missing qfqday; "
            f"received keys {keys}"
        )
    rows = stock_data.get("qfqday") or []
    if not isinstance(rows, list):
        raise AkShareSchemaError("AkShare Tencent qfqday is not a row list.")
    if not rows:
        return pd.DataFrame()
    if any(not isinstance(row, (list, tuple)) or len(row) < 6 for row in rows):
        raise AkShareSchemaError("AkShare Tencent qfq row schema changed.")
    frame = pd.DataFrame([row[:6] for row in rows])
    frame.columns = ["date", "open", "close", "high", "low", "amount"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].isna().any():
        raise AkShareSchemaError("AkShare Tencent qfq response contains invalid dates.")
    duplicate_rows = frame[frame["date"].duplicated(keep=False)]
    if not duplicate_rows.empty and (
        duplicate_rows.groupby("date", sort=False).nunique(dropna=False).max(axis=1)
        > 1
    ).any():
        raise AkShareSchemaError(
            "AkShare Tencent qfq page contains conflicting duplicate dates."
        )
    frame = (
        frame.drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    frame.attrs["raw_count"] = len(rows)
    return frame


def _fetch_tencent(prefixed_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Equivalent bounded-timeout path for AkShare's Tencent daily endpoint.

    AkShare ``stock_zh_a_hist_tx(timeout=...)`` first calls
    ``get_tx_start_year()``, whose requests ignore that timeout. Reuse AkShare's
    decoder and endpoint schema here so every network call is bounded without
    modifying global ``requests`` behavior.
    """
    load_akshare()
    try:
        from akshare.utils import demjson
    except Exception as exc:  # noqa: BLE001 - dependency failure shape varies
        from .common import AkShareUnavailableError

        raise AkShareUnavailableError(
            f"AkShare Tencent decoder is unavailable: {type(exc).__name__}: {exc}"
        ) from exc

    frames: list[pd.DataFrame] = []
    cursor_end = pd.Timestamp(end_date)
    requested_start = pd.Timestamp(start_date)
    previous_earliest: pd.Timestamp | None = None
    for _page_number in range(_TENCENT_MAX_PAGES):
        page = _fetch_tencent_page(
            prefixed_code,
            start_date,
            cursor_end.strftime("%Y-%m-%d"),
            decoder=demjson,
        )
        if page.empty:
            break
        earliest = page["date"].min()
        latest = page["date"].max()
        # Tencent treats ``count`` as authoritative and can backfill rows before
        # the requested start date. That is useful for one-call warmups; keep
        # those rows only long enough to decide whether another page is needed,
        # then apply the caller's exact window below. Rows after the page end
        # would violate the analysis-date cutoff and remain a schema failure.
        if latest > cursor_end:
            raise AkShareSchemaError(
                "AkShare Tencent qfq page returned rows after its requested end."
            )
        if previous_earliest is not None and latest >= previous_earliest:
            raise AkShareSchemaError(
                "AkShare Tencent qfq pagination did not move backward."
            )
        frames.append(page)
        raw_count = int(page.attrs.get("raw_count", len(page)))
        if earliest <= requested_start or raw_count < _TENCENT_PAGE_SIZE:
            break
        previous_earliest = earliest
        cursor_end = earliest - timedelta(days=1)
    else:
        raise AkShareSchemaError(
            f"AkShare Tencent qfq pagination exceeded {_TENCENT_MAX_PAGES} pages."
        )

    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    conflicts = frame.groupby("date", sort=False).nunique(dropna=False).max(axis=1)
    if (conflicts > 1).any():
        raise AkShareSchemaError(
            "AkShare Tencent qfq pages returned conflicting duplicate dates."
        )
    frame = frame[
        (frame["date"] >= pd.Timestamp(start_date))
        & (frame["date"] <= pd.Timestamp(end_date))
    ]
    return frame.drop_duplicates("date", keep="last").sort_values("date")


def fetch_ohlcv(symbol: str, start_date: str, end_date: str) -> OHLCVResult:
    """Fetch validated qfq bars, trying Tencent then Eastmoney, and cache success."""
    canonical, code, exchange = canonical_a_share(symbol)
    requested_start = pd.Timestamp(start_date).strftime("%Y-%m-%d")
    requested_end = pd.Timestamp(end_date).strftime("%Y-%m-%d")
    if requested_start > requested_end:
        raise ValueError(
            f"start_date {requested_start} must not be after end_date {requested_end}."
        )
    expected_end = effective_trade_date(requested_end).strftime("%Y-%m-%d")
    effective_start = min(requested_start, expected_end)
    key = (canonical, effective_start, expected_end)
    cached = _FRAME_CACHE.get(key)
    if cached is not None:
        _FRAME_CACHE.move_to_end(key)
        return OHLCVResult(
            cached.frame.copy(),
            cached.source,
            cached.canonical,
            requested_end,
            cached.effective_end,
            cached.adjustment,
            cached.fallback_reason,
        )

    attempts = (
        ("AkShare / Tencent", lambda: _fetch_tencent(f"{exchange}{code}", effective_start, expected_end)),
        ("AkShare / Eastmoney", lambda: _fetch_eastmoney(code, effective_start, expected_end)),
    )
    errors: list[Exception] = []
    for source_index, (source, fetch) in enumerate(attempts):
        started = time.monotonic()
        try:
            frame = _normalize_frame(
                fetch(),
                symbol=symbol,
                canonical=canonical,
                source=source,
                start_date=effective_start,
                expected_end=expected_end,
            )
            fallback_reason = None
            if source_index > 0:
                primary_error = errors[0]
                fallback_reason = (
                    "Tencent returned no usable observations"
                    if isinstance(primary_error, NoMarketDataError)
                    else "Tencent primary retrieval unavailable"
                )
            result = OHLCVResult(
                frame,
                source,
                canonical,
                requested_end,
                expected_end,
                fallback_reason=fallback_reason,
            )
            _remember(key, result)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "%s healthy for %s: schema=valid rows=%d latest=%s "
                "latency_ms=%d adjustment=qfq",
                source,
                canonical,
                len(frame),
                expected_end,
                elapsed_ms,
            )
            return OHLCVResult(
                frame.copy(),
                source,
                canonical,
                requested_end,
                expected_end,
                fallback_reason=fallback_reason,
            )
        except Exception as exc:  # noqa: BLE001 - internal source fallback boundary
            errors.append(exc)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            status, latest, detail = _health_failure(exc)
            logger.warning(
                "%s unhealthy for %s: status=%s latest=%s latency_ms=%d "
                "error=%s detail=%s",
                source,
                canonical,
                status,
                latest,
                elapsed_ms,
                type(exc).__name__,
                detail,
            )

    if errors and all(isinstance(exc, NoMarketDataError) for exc in errors):
        raise errors[-1]
    if errors and all(isinstance(exc, AkShareUnavailableError) for exc in errors):
        raise errors[-1]
    if errors and all(isinstance(exc, AkShareRateLimitError) for exc in errors):
        raise errors[-1]
    details = "; ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
    raise AkShareRequestError(
        f"All AkShare qfq price sources failed for {canonical}: {details}"
    ) from (errors[-1] if errors else None)


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return AkShare qfq daily bars as compatible CSV plus source metadata."""
    result = fetch_ohlcv(symbol, start_date, end_date)
    output = result.frame.copy()
    output["Date"] = output["Date"].dt.strftime("%Y-%m-%d")
    for column in ("Open", "High", "Low", "Close"):
        output[column] = output[column].round(4)
    header = (
        f"# Stock data for {result.canonical} from {start_date} to {end_date}\n"
        f"# Price adjustment: {result.adjustment}\n"
        "# Volume unit: shares\n"
        f"# Actual data source: {result.source}\n"
        f"# Requested end date: {result.requested_end}\n"
        f"# Effective trading date: {result.effective_end}\n"
        f"# Total records: {len(output)}\n"
        f"# Data retrieved on: {datetime.now(_SHANGHAI).isoformat(timespec='seconds')}\n\n"
    )
    timing = "market-date filtered; qfq adjusted; future rows excluded"
    if result.fallback_reason:
        timing += (
            f"; fallback: {result.fallback_reason}; {ADJUSTMENT_FALLBACK_NOTE}"
        )
    return attach_provenance(
        header + output.to_csv(index=False),
        ProvenanceRecord(
            evidence="get_stock_data",
            source=result.source,
            requested=f"{start_date} to {end_date}",
            effective=f"{output.iloc[0]['Date']} to {result.effective_end}",
            timing=timing,
        ),
    )
