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
iterates each calendar date and filters by securities code. The per-date fetch +
process memoization and the capped window iteration live in :mod:`edinet_common`
(:func:`~edinet_common.documents_on` / :func:`~edinet_common.iter_window_dates`)
so this feed and the large-shareholding signal share one cache. Look-ahead safety
is structural: we never query a date after ``end_date``, and ``submitDateTime`` is
the real filing time.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.provenance import (
    SourceObservation,
    SourceWatermark,
    attach_source_observations,
    attach_source_watermarks,
)

from ..config import get_config
from ..symbol_utils import tokyo_securities_base
from .edinet_common import (
    documents_on,
    filing_detail_line,
    iter_window_dates,
    render_filings,
)
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)
_TOKYO = ZoneInfo("Asia/Tokyo")


def _observation(record: dict) -> SourceObservation | None:
    doc_id = str(record.get("docID") or "").strip()
    if not doc_id:
        return None
    parent_id = str(record.get("parentDocID") or "").strip()
    submitted = str(record.get("submitDateTime") or "").strip()
    try:
        available_at = datetime.fromisoformat(submitted).replace(tzinfo=_TOKYO).isoformat()
    except ValueError:
        return None
    withdrawn = str(
        record.get("withdrawalStatus") or record.get("withdrawStatus") or ""
    ).strip()
    corrected = str(record.get("docInfoEditStatus") or "").strip()
    title = str(record.get("docDescription") or record.get("docTypeCode") or "Disclosure")
    if withdrawn not in {"", "0"}:
        status = "withdrawn"
    elif "訂正" in title or corrected not in {"", "0"}:
        status = "corrected"
    elif parent_id:
        status = "replaced"
    else:
        status = "published"
    return SourceObservation(
        source="EDINET",
        record_id=parent_id or doc_id,
        version_id=f"edinet:{doc_id}",
        status=status,
        published_at=submitted,
        available_at=available_at,
        title=title,
        url=f"https://disclosure2.edinet-fsa.go.jp/WEEK0010.aspx?docID={doc_id}",
        replaces_version_id=(f"edinet:{parent_id}" if parent_id else None),
    )


def _format_filing(record: dict) -> str:
    """Render one EDINET filing as a markdown news item."""
    title = record.get("docDescription") or record.get("docTypeCode") or "Disclosure"
    filer = record.get("filerName") or "Unknown filer"
    line = f"### {title} (filer: {filer})"
    detail = filing_detail_line(record)
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
    dates = list(iter_window_dates(start_date, end_date))
    scanned_start = dates[0] if dates else start_date
    limitations = (
        ("Requested interval exceeded the EDINET 90-calendar-date collection window.",)
        if dates and scanned_start != start_date
        else ()
    )

    # EDINET carries the 5-digit securities code (``99840``); reduce it to the
    # 4-digit base so it compares equal to the ticker's J-Quants code (``9984``).
    retrieved_matches = [
        record
        for date_str in dates
        for record in documents_on(date_str)
        if tokyo_securities_base(record.get("secCode")) == code
    ]
    matches = list(
        {
            str(record.get("docID") or (
                record.get("submitDateTime"),
                record.get("docDescription"),
                record.get("filerName"),
            )): record
            for record in retrieved_matches
        }.values()
    )

    observations = tuple(
        item for item in (_observation(record) for record in matches) if item is not None
    )
    watermark = SourceWatermark(
        source="EDINET",
        scanned_start=scanned_start,
        scanned_end=end_date,
        status="limited" if limitations else "complete",
        limitations=limitations,
        returned_records=len(matches),
        reported_records=len(matches),
    )
    if not matches:
        body = (
            f"No EDINET disclosures found for {ticker} between {scanned_start} and "
            f"{end_date}"
        )
        return attach_source_watermarks(body, watermark)

    # Most recent first, capped like the other news vendors.
    items = render_filings(matches, _format_filing, limit)
    body = (
        f"## {ticker} EDINET disclosures, from {scanned_start} to {end_date}:\n\n{items}"
    )
    body = attach_source_observations(body, *observations)
    return attach_source_watermarks(body, watermark)
