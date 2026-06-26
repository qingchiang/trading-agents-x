"""Per-ticker Japanese disclosure feed backed by EDINET.

J-Quants' Light plan does not serve news or timely disclosure, so for ``.T``
tickers the ``get_news`` tool routes here. EDINET's statutory filings are the
free, official, per-company event stream for Japanese equities. We surface the
filing list (title / type / filer / time) as the news block — not the full XBRL
body, which is a possible later enhancement.

**Coverage scope.** We match on the filing's ``secCode``, which EDINET sets to
the *filer's* securities code. So this surfaces a company's **own** disclosures
(securities reports, quarterly/extraordinary reports it files). Disclosures that
a *third party* files *about* the company — large-shareholding (大量保有) and
tender-offer reports, where ``secCode`` is the filer's code (or empty) and the
target is in ``subjectEdinetCode`` — are **not** captured by this filter. Adding
them would need a ticker→EDINET-code lookup against ``subjectEdinetCode``; that
is a deliberate later enhancement (also relevant to the planned sentiment proxy).

EDINET's document list is date-keyed (no company search), so a window query
iterates each calendar date and filters by securities code. Per-date fetches are
memoized for the process (settled past dates only — see ``_documents_on``) so a
multi-ticker run hits each date once. Look-ahead safety is structural: we never
query a date after ``end_date``, and ``submitDateTime`` is the real filing time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .config import get_config
from .edinet_common import fetch_documents
from .jquants_common import to_jquants_code
from .symbol_utils import tokyo_securities_base

logger = logging.getLogger(__name__)

# Guard against an unbounded window blowing up into one request per day. A news
# block is a short recent window (the sentiment analyst uses 7 days); cap the
# date span we will iterate so a pathological range can't hammer the API.
_MAX_WINDOW_DAYS = 92

# Process-local cache of EDINET's per-date document list, keyed by "YYYY-MM-DD".
# The same trading day is requested once per ticker per window across a run, so
# without this each get_news call would re-fetch overlapping dates.
_documents_cache: dict[str, list[dict]] = {}


def _documents_on(date_str: str, today: str) -> list[dict]:
    """Return EDINET filings for ``date_str``, memoizing settled past dates.

    A past date's filings are immutable, so caching them is safe. We do NOT
    cache ``today`` (or any not-yet-past date): filings keep arriving through the
    day, so a cached early-morning snapshot would mask same-day disclosures for
    the rest of a long-running process. ``today`` is passed in so the caller
    reads the clock once per window, not once per date.
    """
    cached = _documents_cache.get(date_str)
    if cached is not None:
        return cached
    records = fetch_documents(date_str)
    if date_str < today:
        _documents_cache[date_str] = records
    return records


def _iter_dates(start_date: str, end_date: str):
    """Yield each ``YYYY-MM-DD`` from start to end inclusive (capped, oldest first)."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    if (end - start).days > _MAX_WINDOW_DAYS:
        logger.warning(
            "EDINET window %s..%s exceeds %d days; querying only the last %d.",
            start_date, end_date, _MAX_WINDOW_DAYS, _MAX_WINDOW_DAYS,
        )
        start = end - timedelta(days=_MAX_WINDOW_DAYS)
    day = start
    while day <= end:
        yield day.strftime("%Y-%m-%d")
        day += timedelta(days=1)


def _format_filing(record: dict) -> str:
    """Render one EDINET filing as a markdown news item."""
    title = record.get("docDescription") or record.get("docTypeCode") or "Disclosure"
    filer = record.get("filerName") or "Unknown filer"
    line = f"### {title} (filer: {filer})"
    detail = " · ".join(
        part for part in (
            f"Submitted: {record['submitDateTime']}" if record.get("submitDateTime") else "",
            f"EDINET docID: {record['docID']}" if record.get("docID") else "",
        ) if part
    )
    return f"{line}\n{detail}" if detail else line


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return EDINET disclosures for ``ticker`` in ``[start_date, end_date]``.

    Iterates the window day by day, keeping filings whose securities code matches
    the ticker. Returns a formatted markdown block, or an informative "no
    disclosures" line when the company filed nothing in the window (a normal,
    common outcome — not a data-availability failure).
    """
    code = to_jquants_code(ticker)
    limit = get_config()["news_article_limit"]
    today = datetime.now().strftime("%Y-%m-%d")

    # EDINET carries the 5-digit securities code (``99840``); reduce it to the
    # 4-digit base so it compares equal to the ticker's J-Quants code (``9984``).
    matches = [
        record
        for date_str in _iter_dates(start_date, end_date)
        for record in _documents_on(date_str, today)
        if tokyo_securities_base(record.get("secCode")) == code
    ]

    if not matches:
        return (
            f"No EDINET disclosures found for {ticker} between {start_date} and {end_date}"
        )

    # Most recent first, capped like the other news vendors.
    matches.sort(key=lambda r: r.get("submitDateTime") or "", reverse=True)
    items = "\n\n".join(_format_filing(r) for r in matches[:limit])
    return (
        f"## {ticker} EDINET disclosures, from {start_date} to {end_date}:\n\n{items}"
    )
