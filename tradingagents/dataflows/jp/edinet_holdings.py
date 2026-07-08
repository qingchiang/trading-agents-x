"""Per-ticker large-shareholding (大量保有) signal from EDINET for .T sentiment.

When an investor's stake in a listed company crosses 5% (and on subsequent ≥1%
moves), Japanese law requires a large-shareholding report. These are filed *about*
the company by the *shareholder*, so EDINET tags the target in ``subjectEdinetCode``
rather than the filing's ``secCode`` — which is why :mod:`edinet_news` (matching on
``secCode``) does not surface them and we resolve the ticker's EDINET code via
:mod:`edinet_code_map` instead.

This is a sentiment signal: a cluster of new 5%+ positions hints at institutional
accumulation; change reports flag a known holder adjusting. We surface the filing
list (who / type / when) — the actual stake percentage and direction live in the
XBRL body, a possible later enhancement, so the LLM is told to read frequency and
filer identity, not a precise position.

EDINET's document list is date-keyed, so we iterate the window via the shared
:mod:`edinet_common` helpers (one process-wide per-date cache, shared with the
news feed). While iterating we also :func:`~edinet_code_map.learn` every issuer's
own code, the self-heal that keeps :mod:`edinet_code_map` current. Pre-fetched by
the sentiment analyst (not routed), so like the other prefetch sources it must
always return a string and never raise.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..config import get_config
from .edinet_code_map import learn_many, resolve_edinet_code
from .edinet_common import (
    documents_on,
    filing_detail_line,
    iter_window_dates,
    render_filings,
)

logger = logging.getLogger(__name__)

# How far back to scan for large-holding filings. They are infrequent per name, so
# the window is wider than the news block's; it is still capped by edinet_common's
# MAX_WINDOW_DAYS. Each calendar day is one (cached) documents.json fetch.
_LOOK_BACK_DAYS = 30

# 大量保有 report family (提出書類種別コード). Both carry subjectEdinetCode.
_DOC_TYPE_LABELS = {
    "350": "Large-shareholding report (5%+ position)",
    "360": "Large-shareholding change report",
}


def _format_filing(record: dict) -> str:
    """Render one large-holding filing as a markdown item (filer = the holder)."""
    label = _DOC_TYPE_LABELS.get(str(record.get("docTypeCode")), "Large-shareholding filing")
    holder = record.get("filerName") or "Unknown filer"
    line = f"### {label} — filed by {holder}"
    detail = filing_detail_line(record)
    return f"{line}\n{detail}" if detail else line


def get_large_holdings(ticker: str, curr_date: str, look_back_days: int = _LOOK_BACK_DAYS) -> str:
    """Return recent EDINET large-shareholding filings about ``ticker``.

    Tokyo-only (returns "" for non-``.T`` tickers, like the investor-flow proxy —
    a future market supplies its own source). Scans ``[curr_date - look_back_days,
    curr_date]``, learning every issuer's code along the way and keeping filings
    whose ``subjectEdinetCode`` is this ticker. Degrades to a placeholder string on
    any error or when the ticker's EDINET code is unknown; never raises.
    """
    if not str(ticker).upper().endswith(".T"):
        return ""

    try:
        # strptime inside the try so a malformed curr_date degrades rather than
        # escaping (never-raise prefetch contract).
        end = datetime.strptime(curr_date, "%Y-%m-%d")
        start = (end - timedelta(days=look_back_days)).strftime("%Y-%m-%d")

        code = resolve_edinet_code(ticker)
        if code is None:
            # Unknown issuer (new listing not yet in seed or learned cache). Don't
            # scan dozens of dates for a subject we can't match; the self-heal on
            # other runs will fill it in once we have seen any of its filings.
            return f"<no EDINET code on file for {ticker}; large-shareholding lookup skipped>"

        # Materialize the (capped) date list so the reported window matches what
        # was actually scanned — iter_window_dates clamps spans over MAX_WINDOW_DAYS.
        dates = list(iter_window_dates(start, curr_date))
        scanned_start = dates[0] if dates else start

        matches = []
        learned_pairs = []
        for date_str in dates:
            for record in documents_on(date_str):
                learned_pairs.append((record.get("secCode"), record.get("edinetCode")))
                if (
                    record.get("subjectEdinetCode") == code
                    and str(record.get("docTypeCode")) in _DOC_TYPE_LABELS
                ):
                    matches.append(record)
        # Self-heal in one lock + one cache write, not per record.
        learn_many(learned_pairs)
    except Exception as exc:
        logger.warning("Large-holding fetch failed for %s: %s", ticker, exc)
        return f"<large-shareholding data unavailable: {type(exc).__name__}>"

    if not matches:
        return (
            f"No EDINET large-shareholding reports about {ticker} "
            f"between {scanned_start} and {curr_date}"
        )

    items = render_filings(matches, _format_filing, get_config()["news_article_limit"])
    return (
        f"EDINET 大量保有報告書 about {ticker}, {scanned_start} to {curr_date} "
        "(5%+ stakes filed by shareholders; counts/identities below, not precise %):"
        f"\n\n{items}"
    )
