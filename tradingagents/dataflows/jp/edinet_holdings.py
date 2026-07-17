"""Per-ticker ownership & control signal from EDINET for .T sentiment.

Two filing families name their *subject* company in ``subjectEdinetCode`` (not the
filing's own ``secCode``), so :mod:`edinet_news` — which matches on ``secCode`` —
never surfaces them; we resolve the ticker's EDINET code via
:mod:`edinet_code_map` and match on the subject instead:

  * **大量保有 (large-shareholding)** — when an investor's stake crosses 5% (and on
    subsequent ≥1% moves) it files a report *about* the company. A cluster of new
    5%+ positions hints at institutional accumulation; change reports flag a known
    holder adjusting.
  * **公開買付 (tender offer / TOB)** — a takeover bid for the company: a launch
    (公開買付届出書), the target board's opinion (意見表明報告書), a withdrawal
    (公開買付撤回届出書), the result (公開買付報告書), or a 訂正 amendment to any of
    these (a TOB amendment routinely changes the bid price or terms). Highly
    material — a bid is usually a premium offer — and, like 大量保有, tagged against
    the target in ``subjectEdinetCode`` (verified live).

This is a sentiment signal: we surface the filing list (who / type / when). The
precise stake percentage and direction live in the XBRL body (a possible later
enhancement), so the LLM is told to read frequency, filing type, and filer
identity, not an exact position.

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
from .market import is_tokyo_ticker

logger = logging.getLogger(__name__)

# Inclusive [curr_date - 89 days, curr_date] window: exactly 90 calendar dates.
# These filings are sparse and can affect control/positioning for a full quarter;
# each all-market date list is shared through edinet_common's memory/disk cache.
_LOOK_BACK_DAYS = 89

# Filing families (提出書類種別コード) that tag their subject in subjectEdinetCode,
# so matching a ticker's EDINET code surfaces filings *about* it. Codes verified
# against live EDINET (240 = launch, 270 = result — not the reverse). Rare codes
# unseen in live sampling (260, 280) are added on the authority of EDINET's docType
# registry, and are safe either way (one lacking subjectEdinetCode just won't match).
_DOC_TYPE_LABELS = {
    # 大量保有 (large-shareholding)
    "350": "Large-shareholding report (5%+ position)",
    "360": "Large-shareholding change report",
    # 公開買付 (tender offer / TOB), including the 訂正 amendments: unlike a clerical
    # holding correction, a TOB 訂正 routinely changes the bid price / offer period /
    # result, so dropping it would leave an outdated bid state (and vanish entirely
    # if the original filing has aged out of the window). Kept and labelled "amended".
    "240": "Takeover bid launched (TOB, 公開買付届出書)",
    "250": "Takeover bid amended (TOB, 訂正公開買付届出書)",
    "290": "Target board opinion on TOB (意見表明報告書)",
    "300": "Target board opinion amended (訂正意見表明報告書)",
    "260": "Takeover bid withdrawn (TOB, 公開買付撤回届出書)",
    "270": "Takeover bid result (TOB, 公開買付報告書)",
    "280": "Takeover bid result amended (TOB, 訂正公開買付報告書)",
}


def _format_filing(record: dict) -> str:
    """Render one ownership/control filing as a markdown item.

    The filer is whoever filed: the shareholder (大量保有), the bidder (a TOB
    launch/result), or the target itself (its opinion on a bid).
    """
    label = _DOC_TYPE_LABELS.get(str(record.get("docTypeCode")), "Ownership/control filing")
    filer = record.get("filerName") or "Unknown filer"
    line = f"### {label} — filed by {filer}"
    detail = filing_detail_line(record)
    return f"{line}\n{detail}" if detail else line


def get_large_holdings(ticker: str, curr_date: str, look_back_days: int = _LOOK_BACK_DAYS) -> str:
    """Return recent EDINET large-shareholding & tender-offer (TOB) filings about ``ticker``.

    Tokyo-only (returns "" for non-``.T`` tickers, like the investor-flow proxy —
    a future market supplies its own source). Scans ``[curr_date - look_back_days,
    curr_date]``, learning every issuer's code along the way and keeping filings
    whose ``subjectEdinetCode`` is this ticker. Degrades to a placeholder string on
    any error or when the ticker's EDINET code is unknown; never raises.
    """
    if not is_tokyo_ticker(ticker):
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
        # was actually scanned — iter_window_dates clamps to 90 inclusive dates.
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
            f"No EDINET large-shareholding or tender-offer filings about {ticker} "
            f"between {scanned_start} and {curr_date}"
        )

    items = render_filings(matches, _format_filing, get_config()["news_article_limit"])
    return (
        f"EDINET ownership & control filings about {ticker}, {scanned_start} to {curr_date} "
        "(大量保有 5%+ stakes and 公開買付 takeover bids; type/filer/date below, "
        "stake % not parsed):"
        f"\n\n{items}"
    )
