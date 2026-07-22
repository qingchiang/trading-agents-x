"""Japan MOF constant-maturity yield CSV parsing and bounded raw-data cache."""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import math
import os
import re
import tempfile
from datetime import date, datetime, time, timedelta
from io import StringIO
from typing import NamedTuple
from urllib.request import Request
from zoneinfo import ZoneInfo

from ..cn.common import REQUEST_TIMEOUT
from ..config import get_config
from .calendar import (
    add_government_business_days,
    is_government_business_day,
    is_tse_open,
)
from .http_util import USER_AGENT, fetch_bytes

logger = logging.getLogger(__name__)

CURRENT_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
)
HISTORY_URL = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    "historical/jgbcme_all.csv"
)

_TOKYO = ZoneInfo("Asia/Tokyo")
_PUBLICATION_TIME = time(9, 30)
_HISTORY_TTL = timedelta(days=30)
_RETRY_TTL = timedelta(hours=1)
_DATE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}$")


class MofDataError(RuntimeError):
    """Base class for sanitized MOF retrieval and schema failures."""


class MofRequestError(MofDataError):
    """The MOF CSV could not be retrieved."""


class MofSchemaError(MofDataError):
    """The MOF CSV no longer matches the validated shape."""


class _CacheEntry(NamedTuple):
    points: list[tuple[str, str]]
    expires_at: datetime


_memory_cache: dict[str, _CacheEntry] = {}


def tokyo_now(now: datetime | None = None) -> datetime:
    """Normalize an injectable clock to an aware Tokyo datetime."""
    current = now or datetime.now(_TOKYO)
    if current.tzinfo is None:
        return current.replace(tzinfo=_TOKYO)
    return current.astimezone(_TOKYO)


def publication_datetime(observation: date) -> datetime:
    """Return when an observation is expected to become public."""
    publication_date = add_government_business_days(observation, 1)
    return datetime.combine(publication_date, _PUBLICATION_TIME, _TOKYO)


def analysis_as_of(requested_end: date, now: datetime | None = None) -> datetime:
    """Resolve end-of-day historical visibility and wall-clock live visibility."""
    current = tokyo_now(now)
    if requested_end < current.date():
        return datetime.combine(requested_end, time.max, _TOKYO)
    return current


def _next_publication_boundary(now: datetime) -> datetime:
    candidate = now.date()
    while True:
        if is_government_business_day(candidate):
            boundary = datetime.combine(candidate, _PUBLICATION_TIME, _TOKYO)
            if boundary > now:
                return boundary
        candidate += timedelta(days=1)


def _latest_publication_boundary(now: datetime) -> datetime:
    candidate = now.date()
    while True:
        if is_government_business_day(candidate):
            boundary = datetime.combine(candidate, _PUBLICATION_TIME, _TOKYO)
            if boundary <= now:
                return boundary
        candidate -= timedelta(days=1)


def _first_publication_boundary(year: int, month: int) -> datetime:
    candidate = date(year, month, 1)
    while not is_government_business_day(candidate):
        candidate += timedelta(days=1)
    return datetime.combine(candidate, _PUBLICATION_TIME, _TOKYO)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _last_tse_observation_of_previous_month(year: int, month: int) -> date:
    candidate = date(year, month, 1) - timedelta(days=1)
    while not is_tse_open(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _cache_expiry(kind: str, points: list[tuple[str, str]], now: datetime) -> datetime:
    """Choose the next source-aware refresh boundary for a successful payload."""
    if kind == "current":
        expiry = _next_publication_boundary(now)
        latest_observation = date.fromisoformat(points[-1][0])
        if publication_datetime(latest_observation) < _latest_publication_boundary(now):
            expiry = min(expiry, now + _RETRY_TTL)
        return expiry

    next_year, next_month = _next_month(now.year, now.month)
    expiry = min(
        now + _HISTORY_TTL,
        _first_publication_boundary(next_year, next_month),
    )
    latest_observation = date.fromisoformat(points[-1][0])
    expected_observation = _last_tse_observation_of_previous_month(
        now.year, now.month
    )
    expected_publication = publication_datetime(expected_observation)
    if now < expected_publication:
        expiry = min(expiry, expected_publication)
    elif latest_observation < expected_observation:
        expiry = min(expiry, now + _RETRY_TTL)
    return expiry


def _cache_path(kind: str) -> str:
    return os.path.join(
        get_config()["data_cache_dir"], "macro", "jp", f"mof_{kind}.json"
    )


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _validate_cached_points(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("empty points")
    points: list[tuple[str, str]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("invalid point")
        observation = date.fromisoformat(str(point[0]))
        numeric = float(point[1])
        if not math.isfinite(numeric):
            raise ValueError("invalid value")
        points.append((observation.isoformat(), f"{numeric:g}"))
    if points != sorted(points):
        raise ValueError("unsorted points")
    return points


def _cache_get(kind: str, now: datetime) -> list[tuple[str, str]] | None:
    entry = _memory_cache.get(kind)
    if entry is not None:
        if now < entry.expires_at:
            return entry.points
        del _memory_cache[kind]

    path = _cache_path(kind)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None:
            raise ValueError("naive expiry")
        expires_at = expires_at.astimezone(_TOKYO)
        if now >= expires_at:
            _remove(path)
            return None
        points = _validate_cached_points(payload["points"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    _memory_cache[kind] = _CacheEntry(points, expires_at)
    return points


def _cache_put(
    kind: str,
    points: list[tuple[str, str]],
    expires_at: datetime,
) -> None:
    """Persist only a validated non-empty response, atomically and best-effort."""
    _memory_cache[kind] = _CacheEntry(points, expires_at)
    path = _cache_path(kind)
    disk_dir = os.path.dirname(path)
    try:
        os.makedirs(disk_dir, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=disk_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"expires_at": expires_at.isoformat(), "points": points},
                    handle,
                )
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError):
            _remove(temporary)
            raise
    except (OSError, TypeError, ValueError):
        logger.debug("MOF %s cache write skipped", kind)


def clear_memory_cache() -> None:
    """Clear process-local raw-file entries without deleting cross-run cache files."""
    _memory_cache.clear()


def parse_csv(body: bytes) -> list[tuple[str, str]]:
    """Parse one English MOF CSV and return its validated daily 10Y series."""
    try:
        text = body.decode("cp932")
    except UnicodeDecodeError as exc:
        raise MofSchemaError("MOF JP10Y CSV encoding changed.") from exc

    rows = list(csv.reader(StringIO(text)))
    header_index = None
    ten_year_index = None
    for index, row in enumerate(rows):
        normalized = [cell.strip() for cell in row]
        if normalized and normalized[0] == "Date" and "10Y" in normalized:
            header_index = index
            ten_year_index = normalized.index("10Y")
            break
    if header_index is None or ten_year_index is None:
        raise MofSchemaError("MOF JP10Y CSV header changed.")

    observations: dict[date, str] = {}
    for row in rows[header_index + 1 :]:
        if not row or not row[0].strip():
            continue
        date_text = row[0].strip()
        if not _DATE_RE.fullmatch(date_text):
            if all(not cell.strip() for cell in row[1:]):
                continue
            raise MofSchemaError("MOF JP10Y CSV contains an invalid date row.")
        if len(row) <= ten_year_index:
            raise MofSchemaError("MOF JP10Y CSV row is missing the 10Y column.")
        try:
            observation = datetime.strptime(date_text, "%Y/%m/%d").date()
        except ValueError as exc:
            raise MofSchemaError("MOF JP10Y CSV contains an invalid date.") from exc
        raw_value = row[ten_year_index].strip()
        if raw_value in {"", "-"}:
            continue
        try:
            numeric = float(raw_value)
        except ValueError as exc:
            raise MofSchemaError("MOF JP10Y CSV contains an invalid yield.") from exc
        if not math.isfinite(numeric) or not -10 < numeric < 30:
            raise MofSchemaError("MOF JP10Y CSV contains an implausible yield.")
        rendered = f"{numeric:g}"
        existing = observations.get(observation)
        if existing is not None and existing != rendered:
            raise MofSchemaError("MOF JP10Y CSV contains conflicting duplicate dates.")
        observations[observation] = rendered
    return [(observation.isoformat(), observations[observation]) for observation in sorted(observations)]


def _download(kind: str) -> bytes:
    url = CURRENT_URL if kind == "current" else HISTORY_URL
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*;q=0.1"},
    )
    body = fetch_bytes(request, REQUEST_TIMEOUT, f"MOF JP10Y {kind} CSV")
    if body is None:
        raise MofRequestError("MOF JP10Y retrieval unavailable.")
    return body


def _load(kind: str, now: datetime) -> list[tuple[str, str]]:
    cached = _cache_get(kind, now)
    if cached is not None:
        return cached
    points = parse_csv(_download(kind))
    if not points:
        raise MofSchemaError("MOF JP10Y CSV returned no usable observations.")
    _cache_put(kind, points, _cache_expiry(kind, points, now))
    return points


def _current_month_observation_is_visible(as_of: datetime) -> bool:
    candidate = date(as_of.year, as_of.month, 1)
    while candidate <= as_of.date():
        if is_tse_open(candidate) and publication_datetime(candidate) <= as_of:
            return True
        candidate += timedelta(days=1)
    return False


def fetch_points(
    start: date,
    end: date,
    *,
    as_of: datetime,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Load the required MOF files, merge them, and apply publication-time PIT."""
    current = tokyo_now(now)
    current_month = date(current.year, current.month, 1)
    merged: dict[str, str] = {}
    history_points: list[tuple[str, str]] = []
    if start < current_month or end < current_month:
        history_points = _load("history", current)
        merged.update(history_points)

    if end >= current_month:
        current_observation_visible = _current_month_observation_is_visible(as_of)
        history_covers_latest_publication = bool(history_points) and (
            publication_datetime(date.fromisoformat(history_points[-1][0]))
            >= _latest_publication_boundary(as_of)
        )

        # Around month-end the history file can still stop two months back while
        # the small "current" file contains the just-finished month.  Keep using
        # it as a bridge until history absorbs those observations.  Once history
        # is current, do not fetch an empty new-month file before its first point
        # can legitimately be published.
        current_required = current_observation_visible or (
            start < current_month and not history_covers_latest_publication
        )
        if current_required:
            for observation_text, value in _load("current", current):
                existing = merged.get(observation_text)
                if existing is not None and existing != value:
                    raise MofSchemaError(
                        "MOF JP10Y files disagree on an overlapping date."
                    )
                merged[observation_text] = value

    if not merged:
        return []

    points = []
    for observation_text, value in sorted(merged.items()):
        observation = date.fromisoformat(observation_text)
        if start <= observation <= end and publication_datetime(observation) <= as_of:
            points.append((observation_text, value))
    return points
