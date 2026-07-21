"""Bounded direct parser for Sina's per-stock institutional-rating page."""

from __future__ import annotations

import time
from collections import OrderedDict
from datetime import datetime
from io import BytesIO
from threading import RLock

import pandas as pd
import requests

from .common import REQUEST_TIMEOUT, AkShareSchemaError, call_with_retry, canonical_a_share

_RATING_URL = "https://stock.finance.sina.com.cn/stock/go.php/vIR_StockSearch/key/{code}.phtml"
_UA = "Mozilla/5.0 trading-agents-x/0.3.0"
_CACHE_TTL_SECONDS = 15 * 60
_CACHE_MAXSIZE = 128
_CACHE: OrderedDict[tuple[str, str], tuple[float, tuple[dict, ...]]] = OrderedDict()
_CACHE_LOCK = RLock()


def _request_page(code: str) -> bytes:
    def request() -> bytes:
        response = requests.get(
            _RATING_URL.format(code=code),
            params={"num": 100, "p": 1},
            headers={"User-Agent": _UA},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.content

    return call_with_retry(request, label="Sina institutional ratings")


def _code(value) -> str:
    parsed = pd.to_numeric(value, errors="coerce")
    return "" if pd.isna(parsed) else f"{int(parsed):06d}"


def _target_range(value) -> tuple[object, object]:
    if value is None or pd.isna(value):
        return None, None
    text = str(value).strip().replace("—", "-").replace("–", "-")
    values = [pd.to_numeric(item.strip(), errors="coerce") for item in text.split("-")]
    numeric = [float(item) for item in values if not pd.isna(item)]
    if not numeric:
        return None, None
    return (numeric[0], numeric[-1]) if len(numeric) > 1 else (numeric[0], numeric[0])


def _parse_rows(content: bytes, code: str) -> list[dict]:
    try:
        tables = pd.read_html(BytesIO(content), header=0)
    except (ValueError, ImportError) as exc:
        raise AkShareSchemaError("Sina institutional-rating page has no readable table.") from exc
    frame = next(
        (
            table.rename(columns=lambda name: str(name).strip().removesuffix("↓"))
            for table in tables
            if {"股票代码", "评级日期↓", "最新评级", "评级机构"}.issubset(table.columns)
            or {"股票代码", "评级日期", "最新评级", "评级机构"}.issubset(table.columns)
        ),
        None,
    )
    if frame is None:
        raise AkShareSchemaError(
            "Sina institutional-rating table is missing code/date/rating/institution columns."
        )
    normalized_codes = frame["股票代码"].map(_code)
    nonempty_codes = {item for item in normalized_codes if item}
    if nonempty_codes and nonempty_codes != {code}:
        raise AkShareSchemaError("Sina institutional-rating page returned another stock code.")

    rows = []
    for (_, record), record_code in zip(frame.iterrows(), normalized_codes, strict=True):
        if record_code != code:
            continue
        published = pd.to_datetime(record.get("评级日期"), errors="coerce")
        if pd.isna(published):
            continue
        rating = str(record.get("最新评级") or "").strip()
        institution = str(record.get("评级机构") or "").strip()
        if (
            not rating
            or not institution
            or rating.casefold() == "nan"
            or institution.casefold() == "nan"
        ):
            continue
        target_low, target_high = _target_range(record.get("目标价"))
        rows.append(
            {
                "code": code,
                "name": str(record.get("股票名称") or "").strip(),
                "published": published.date(),
                "institution": institution,
                "analyst": str(record.get("分析师") or "").strip(),
                "rating": rating,
                "target_low": target_low,
                "target_high": target_high,
            }
        )

    by_institution: dict[str, list[dict]] = {}
    for row in rows:
        by_institution.setdefault(row["institution"], []).append(row)
    for institution_rows in by_institution.values():
        for row in institution_rows:
            older = [
                candidate
                for candidate in institution_rows
                if candidate["published"] < row["published"]
            ]
            if not older:
                row["rating_change"] = "initiated / prior rating unavailable"
                continue
            prior_date = max(candidate["published"] for candidate in older)
            prior_ratings = {
                candidate["rating"]
                for candidate in older
                if candidate["published"] == prior_date
            }
            if len(prior_ratings) != 1:
                row["rating_change"] = "prior same-date ratings ambiguous"
                continue
            prior = next(iter(prior_ratings))
            if prior == row["rating"]:
                row["rating_change"] = f"reiterated {row['rating']}"
            else:
                row["rating_change"] = f"{prior} -> {row['rating']}"
    return rows


def rating_rows(ticker: str, start_date: str, end_date: str) -> list[dict]:
    """Return Sina rating records inside an inclusive publication-date window."""
    _canonical, code, _exchange = canonical_a_share(ticker)
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    key = (code, end.isoformat())
    with _CACHE_LOCK:
        now = time.monotonic()
        cached = _CACHE.get(key)
        if cached is not None and now < cached[0]:
            rows = [dict(row) for row in cached[1]]
            _CACHE.move_to_end(key)
        else:
            if cached is not None:
                del _CACHE[key]
            rows = _parse_rows(_request_page(code), code)
            _CACHE[key] = (now + _CACHE_TTL_SECONDS, tuple(dict(row) for row in rows))
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_MAXSIZE:
                _CACHE.popitem(last=False)
    return [dict(row) for row in rows if start <= row["published"] <= end]


def _clear_rating_cache() -> None:
    """Clear the process-local rating cache for deterministic tests."""
    with _CACHE_LOCK:
        _CACHE.clear()
