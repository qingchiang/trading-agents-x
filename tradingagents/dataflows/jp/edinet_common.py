"""Shared EDINET (Japanese statutory disclosure, API v2) helpers.

EDINET is the Financial Services Agency's electronic disclosure system. Its v2
API authenticates with a subscription key sent in the ``Ocp-Apim-Subscription-Key``
header (env ``EDINET_API_KEY``). The document-list endpoint is **date-keyed only**
— ``documents.json?date=YYYY-MM-DD&type=2`` returns every filing submitted that
day — so per-company queries iterate dates and filter by securities code.

This module backs both :mod:`edinet_news` and :mod:`edinet_holdings`. Their
all-market per-date lists share a bounded memory LRU and a cross-run gzip disk
cache; the same auth/request layer may back full XBRL parsing later.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import os
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from tradingagents.provenance import SourceObservation
from tradingagents.research_sources import JapaneseResearchSource

from ..config import get_config
from ..errors import VendorNotConfiguredError, VendorRateLimitError
from .calendar import tokyo_today

logger = logging.getLogger(__name__)

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"

# Network timeout (seconds) so a stalled request can't hang the CLI/agents.
REQUEST_TIMEOUT = 30

# Guard against an unbounded window blowing up into one request per day. This is
# an inclusive count: [end - 89 days, end] contains exactly 90 calendar dates.
MAX_WINDOW_CALENDAR_DAYS = 90

_CACHE_SCHEMA = "edinet-documents/v2"
_MEMORY_MAX_DATES = 180
_DISK_MAX_FILES = 400
_LIVE_MEMORY_TTL_SECONDS = 60.0
_PRUNE_EVERY_WRITES = 25
_TMP_ORPHAN_SECONDS = 3600.0
_TOKYO = ZoneInfo("Asia/Tokyo")


def source_observation(
    record: dict,
) -> tuple[SourceObservation | None, str | None]:
    """Build the shared EDINET Source Record identity and availability proof."""

    doc_id = str(record.get("docID") or "").strip()
    if not doc_id:
        return None, "EDINET returned a document without a native document identifier."
    parent_id = str(record.get("parentDocID") or "").strip()
    submitted = str(record.get("submitDateTime") or "").strip()
    withdrawn = str(
        record.get("withdrawalStatus") or record.get("withdrawStatus") or ""
    ).strip()
    corrected = str(record.get("docInfoEditStatus") or "").strip()
    operation_at = str(record.get("opeDateTime") or "").strip()
    is_operation = withdrawn not in {"", "0"} or corrected not in {"", "0"}
    if is_operation and not operation_at:
        return (
            None,
            f"EDINET document {doc_id} has an operation without a safe availability "
            "timestamp.",
        )
    availability_text = operation_at if is_operation else submitted
    try:
        available = datetime.fromisoformat(availability_text)
    except ValueError:
        return None, f"EDINET document {doc_id} has an invalid availability timestamp."
    if available.utcoffset() is None:
        available = available.replace(tzinfo=_TOKYO)
    title = str(record.get("docDescription") or record.get("docTypeCode") or "Disclosure")
    if withdrawn not in {"", "0"}:
        status = "withdrawn"
    elif "訂正" in title or corrected not in {"", "0"}:
        status = "corrected"
    else:
        status = "published"
    return (
        SourceObservation(
            source=JapaneseResearchSource.EDINET,
            record_id=parent_id or doc_id,
            version_id=f"edinet:{doc_id}",
            status=status,
            published_at=submitted,
            available_at=available.isoformat(),
            title=title,
            availability_basis=(
                "EDINET operation timestamp"
                if is_operation
                else "EDINET submission timestamp"
            ),
            url=f"https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID={doc_id}",
            native_record_id=doc_id,
        ),
        None,
    )


class EDINETNotConfiguredError(VendorNotConfiguredError):
    """Raised when EDINET is selected but ``EDINET_API_KEY`` is unset/rejected."""
    pass


class EDINETRateLimitError(VendorRateLimitError):
    """Raised when the EDINET API rate limit is exceeded (HTTP 429)."""
    pass


def get_api_key() -> str:
    """Return the EDINET v2 subscription key from the environment."""
    key = os.getenv("EDINET_API_KEY")
    if not key:
        raise EDINETNotConfiguredError(
            "EDINET_API_KEY environment variable is not set. Issue a subscription "
            "key from the EDINET API registration page (https://api.edinet-fsa.go.jp)."
        )
    return key


def _request(path: str, params: dict) -> dict:
    """GET ``path`` with the subscription-key header; map auth/rate-limit to typed errors."""
    resp = requests.get(
        f"{EDINET_API_BASE}{path}",
        params=params,
        headers={"Ocp-Apim-Subscription-Key": get_api_key()},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise EDINETRateLimitError(f"EDINET rate limit exceeded for {path}.")
    if resp.status_code in (401, 403):
        raise EDINETNotConfiguredError(
            f"EDINET rejected the subscription key ({resp.status_code}) for {path}. "
            "Check EDINET_API_KEY."
        )
    resp.raise_for_status()
    return resp.json()


def fetch_documents(date_str: str) -> list[dict]:
    """Return every document filed on ``date_str`` (``YYYY-MM-DD``).

    Uses ``type=2`` (metadata + document list). Dates with no filings (weekends,
    holidays) return an empty list rather than erroring.
    """
    body = _request("/documents.json", {"date": date_str, "type": 2})
    return body.get("results") or []


# Process-local LRU. Values are ``(records, monotonic_expiry)``; settled dates use
# ``None`` expiry while today/future gets a short TTL and is never written to disk.
# The public-ish name is retained because existing tests clear it between cases.
_documents_cache: OrderedDict[str, tuple[list[dict], float | None]] = OrderedDict()
_memory_lock = threading.Lock()

# Fixed lock stripes give same-date calls single-flight semantics without an
# unbounded date->Lock map. Hash collisions merely serialize two unrelated days.
_date_locks = tuple(threading.Lock() for _ in range(64))

_disk_state_lock = threading.Lock()
_pruned_dirs: set[str] = set()
_writes_by_dir: dict[str, int] = {}


def _canonical_date(date_str: str) -> str:
    """Validate a date before using it as an API parameter or path component."""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")


def _is_settled(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").date() < tokyo_today()


def _memory_get(date_str: str) -> list[dict] | None:
    with _memory_lock:
        entry = _documents_cache.get(date_str)
        if entry is None:
            return None
        records, expiry = entry
        if expiry is not None and time.monotonic() >= expiry:
            del _documents_cache[date_str]
            return None
        _documents_cache.move_to_end(date_str)
        return records


def _memory_put(date_str: str, records: list[dict], *, settled: bool) -> None:
    expiry = None if settled else time.monotonic() + _LIVE_MEMORY_TTL_SECONDS
    with _memory_lock:
        _documents_cache[date_str] = (records, expiry)
        _documents_cache.move_to_end(date_str)
        while len(_documents_cache) > _MEMORY_MAX_DATES:
            _documents_cache.popitem(last=False)


def _disk_dir() -> str:
    return os.path.join(
        get_config()["data_cache_dir"], "edinet", "documents", "v2"
    )


def _disk_file(date_str: str) -> str:
    return os.path.join(_disk_dir(), f"{date_str}.json.gz")


def _remove(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


def _disk_get(date_str: str) -> list[dict] | None:
    """Read a settled date from disk; corruption is a safe cache miss."""
    if not _is_settled(date_str):
        return None
    path = _disk_file(date_str)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not (
            isinstance(payload, dict)
            and payload.get("schema") == _CACHE_SCHEMA
            and payload.get("date") == date_str
            and isinstance(payload.get("records"), list)
        ):
            raise ValueError("invalid EDINET document cache envelope")
        # Disk LRU: a hit should make the file recent, but a read-only cache must
        # still remain usable even if touching its mtime is denied.
        with contextlib.suppress(OSError):
            os.utime(path, None)
        return payload["records"]
    except FileNotFoundError:
        return None
    except (EOFError, OSError, TypeError, ValueError) as exc:
        logger.warning("EDINET document cache unreadable for %s: %s", date_str, exc)
        _remove(path)
        return None


def _disk_put(date_str: str, records: list[dict]) -> None:
    """Persist one settled public document list atomically, best-effort."""
    if not _is_settled(date_str):
        return
    disk_dir = _disk_dir()
    path = _disk_file(date_str)
    tmp = ""
    try:
        os.makedirs(disk_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=disk_dir, suffix=".tmp")
        os.close(fd)
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(
                {"schema": _CACHE_SCHEMA, "date": date_str, "records": records},
                fh,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("EDINET document cache write skipped for %s: %s", date_str, exc)
        if tmp:
            _remove(tmp)
        return

    with _disk_state_lock:
        writes = _writes_by_dir.get(disk_dir, 0) + 1
        should_prune = disk_dir not in _pruned_dirs or writes >= _PRUNE_EVERY_WRITES
        _writes_by_dir[disk_dir] = 0 if should_prune else writes
        if should_prune:
            _pruned_dirs.add(disk_dir)
    if should_prune:
        _prune_disk(disk_dir)


def _prune_disk(disk_dir: str) -> None:
    """Bound persisted dates and reclaim abandoned atomic-write temp files."""
    try:
        names = os.listdir(disk_dir)
    except OSError:
        return
    cache_files = [
        os.path.join(disk_dir, name) for name in names if name.endswith(".json.gz")
    ]
    if len(cache_files) > _DISK_MAX_FILES:
        with contextlib.suppress(OSError):
            cache_files.sort(key=os.path.getmtime)
            for path in cache_files[: len(cache_files) - _DISK_MAX_FILES]:
                _remove(path)
    cutoff = time.time() - _TMP_ORPHAN_SECONDS
    for name in names:
        if not name.endswith(".tmp"):
            continue
        path = os.path.join(disk_dir, name)
        with contextlib.suppress(OSError):
            if os.path.getmtime(path) < cutoff:
                _remove(path)


def documents_on(date_str: str) -> list[dict]:
    """Return a date's filings via memory, disk, then one single-flight fetch.

    Settled dates persist across runs. Today's still-changing list is memory-only
    for 60 seconds, enough for News/Sentiment reuse without pinning an early-day
    snapshot. Cache failures always degrade to the vendor request.
    """
    date_str = _canonical_date(date_str)
    cached = _memory_get(date_str)
    if cached is not None:
        return cached

    lock = _date_locks[hash(date_str) % len(_date_locks)]
    with lock:
        # Another analyst may have completed while this caller waited.
        cached = _memory_get(date_str)
        if cached is not None:
            return cached
        settled = _is_settled(date_str)
        if settled:
            cached = _disk_get(date_str)
            if cached is not None:
                _memory_put(date_str, cached, settled=True)
                return cached
        records = fetch_documents(date_str)
        _memory_put(date_str, records, settled=settled)
        if settled:
            _disk_put(date_str, records)
        return records


def iter_window_dates(start_date: str, end_date: str) -> Iterator[str]:
    """Yield each ``YYYY-MM-DD`` from start to end inclusive (capped, oldest first).

    The span is clamped to the most recent
    :data:`MAX_WINDOW_CALENDAR_DAYS` inclusive dates so an oversized range
    degrades to a bounded number of requests rather than thousands.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    max_offset = MAX_WINDOW_CALENDAR_DAYS - 1
    if (end - start).days > max_offset:
        logger.warning(
            "EDINET window %s..%s exceeds %d calendar dates; querying only the last %d.",
            start_date,
            end_date,
            MAX_WINDOW_CALENDAR_DAYS,
            MAX_WINDOW_CALENDAR_DAYS,
        )
        start = end - timedelta(days=max_offset)
    day = start
    while day <= end:
        yield day.strftime("%Y-%m-%d")
        day += timedelta(days=1)


def filing_detail_line(record: dict) -> str:
    """Render the shared ``Submitted · docID`` detail tail of an EDINET filing.

    The per-ticker news feed and the large-shareholding signal render filings with
    different headers but the same metadata tail, so that tail lives here to keep
    the two in lockstep. Returns "" when neither field is present.
    """
    return " · ".join(
        part for part in (
            f"Submitted: {record['submitDateTime']}" if record.get("submitDateTime") else "",
            f"EDINET docID: {record['docID']}" if record.get("docID") else "",
        ) if part
    )


def render_filings(records: list[dict], format_fn, limit: int) -> str:
    """Sort ``records`` newest-first, cap at ``limit``, and join with blank lines.

    Shared by both EDINET feeds: ``format_fn`` renders one record's header+detail,
    ``limit`` is the per-feed item cap (``news_article_limit``). ``submitDateTime``
    is the real filing time, so sorting on it is also the recency order callers want.
    """
    ordered = sorted(records, key=lambda r: r.get("submitDateTime") or "", reverse=True)
    return "\n\n".join(format_fn(r) for r in ordered[:limit])
