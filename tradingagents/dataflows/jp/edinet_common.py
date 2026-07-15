"""Shared EDINET (Japanese statutory disclosure, API v2) helpers.

EDINET is the Financial Services Agency's electronic disclosure system. Its v2
API authenticates with a subscription key sent in the ``Ocp-Apim-Subscription-Key``
header (env ``EDINET_API_KEY``). The document-list endpoint is **date-keyed only**
— ``documents.json?date=YYYY-MM-DD&type=2`` returns every filing submitted that
day — so per-company queries iterate dates and filter by securities code.

This module backs :mod:`edinet_news` (per-ticker disclosure feed) today; the
same auth/request layer is intended to back full XBRL statement parsing later
(the deferred ``/fins/details`` alternative).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime, timedelta

import requests

from ..errors import VendorNotConfiguredError, VendorRateLimitError
from ..utils import get_current_date

logger = logging.getLogger(__name__)

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"

# Network timeout (seconds) so a stalled request can't hang the CLI/agents.
REQUEST_TIMEOUT = 30

# Guard against an unbounded window blowing up into one request per day. EDINET's
# document list is date-keyed (no company search), so a window query iterates each
# calendar date; cap the span so a pathological range can't hammer the API.
MAX_WINDOW_DAYS = 92


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


# Process-local cache of EDINET's per-date document list, keyed by "YYYY-MM-DD".
# EDINET has no per-company search, so every feature that reads filings (per-ticker
# news, large-shareholding signal) iterates the same dates; a multi-source ``.T``
# run would otherwise re-fetch each overlapping date once per consumer. Shared here
# so they all hit a given date at most once. Tests clear it between cases.
_documents_cache: dict[str, list[dict]] = {}


def documents_on(date_str: str) -> list[dict]:
    """Return EDINET filings for ``date_str``, memoizing settled past dates.

    A past date's filings are immutable, so caching them is safe. We do NOT cache
    today (or any not-yet-past date): filings keep arriving through the day, so a
    cached early-morning snapshot would mask same-day disclosures for the rest of a
    long-running process. The "is this date settled?" rule is the cache's own
    concern, so the clock is read here rather than threaded through every caller.
    """
    cached = _documents_cache.get(date_str)
    if cached is not None:
        return cached
    records = fetch_documents(date_str)
    if date_str < get_current_date():
        _documents_cache[date_str] = records
    return records


def iter_window_dates(start_date: str, end_date: str) -> Iterator[str]:
    """Yield each ``YYYY-MM-DD`` from start to end inclusive (capped, oldest first).

    The span is clamped to the most recent :data:`MAX_WINDOW_DAYS` so an oversized
    range degrades to a bounded number of requests rather than thousands.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if (end - start).days > MAX_WINDOW_DAYS:
        logger.warning(
            "EDINET window %s..%s exceeds %d days; querying only the last %d.",
            start_date, end_date, MAX_WINDOW_DAYS, MAX_WINDOW_DAYS,
        )
        start = end - timedelta(days=MAX_WINDOW_DAYS)
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
