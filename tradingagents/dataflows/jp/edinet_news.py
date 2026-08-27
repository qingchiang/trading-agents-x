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

from ..config import get_config
from ..symbol_utils import tokyo_securities_base
from .edinet_common import (
    documents_on,
    filing_detail_line,
    filing_period_detail,
    iter_window_dates,
    render_filings,
)
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)


def _format_filing(record: dict) -> str:
    """Render one EDINET filing as a markdown news item."""
    title = record.get("docDescription") or record.get("docTypeCode") or "Disclosure"
    filer = record.get("filerName") or "Unknown filer"
    line = f"### {title} (filer: {filer})"
    detail = "\n".join(
        part for part in (filing_detail_line(record), filing_period_detail(record)) if part
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
    dates = list(iter_window_dates(start_date, end_date))
    scanned_start = dates[0] if dates else start_date

    # EDINET carries the 5-digit securities code (``99840``); reduce it to the
    # 4-digit base so it compares equal to the ticker's J-Quants code (``9984``).
    matches = [
        record
        for date_str in dates
        for record in documents_on(date_str)
        if tokyo_securities_base(record.get("secCode")) == code
    ]

    if not matches:
        return (
            f"No EDINET disclosures found for {ticker} between {scanned_start} and "
            f"{end_date}"
        )

    # Most recent first, capped like the other news vendors.
    items = render_filings(matches, _format_filing, limit)
    return (
        f"## {ticker} EDINET disclosures, from {scanned_start} to {end_date}:\n\n{items}"
    )
