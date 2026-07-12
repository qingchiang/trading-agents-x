"""Combined per-ticker Japanese news: EDINET statutory filings + media headlines.

The vendor router is an ordered fallback (first success wins), so a plain
``edinet_news,yfinance`` chain can only ever return ONE source — EDINET always
answers (even "no disclosures"), so the other feeds never run. This assembler
composes them instead: EDINET statutory filings, TDnet timely disclosures
(適時開示: earnings/guidance/M&A), *and* Google-News media reporting
(journalism/analyst coverage) — the complementary halves of "per-stock news" for
a Tokyo name.

Each sub-feed is called defensively: EDINET needs a key and can raise (missing
key, rate limit, network), while TDnet and Google News need none — so one source
failing must not suppress the others. We combine whichever sub-feeds returned
data and raise ``NoMarketDataError`` only when none did, letting the router fall
through to yfinance (English media) as a last resort.
"""

from __future__ import annotations

import logging

from ..errors import NoMarketDataError
from .edinet_news import get_news as _edinet_news
from .google_news import get_news as _google_news
from .tdnet_news import get_news as _tdnet_news

logger = logging.getLogger(__name__)

# A sub-feed emits a "## …" header only when it has items (a "No … found" line
# otherwise), so this prefix tells "has data" from "empty"/failed without
# re-fetching. Kept in sync with the sub-feeds' headers by their tests.
_DATA_PREFIX = "## "


def _safe_feed(fetch, ticker: str, start_date: str, end_date: str) -> str:
    """Run one sub-feed, degrading any failure to an empty string.

    An unguarded EDINET error (e.g. ``EDINET_API_KEY`` unset — expected on a
    keyless run — or a rate limit) would otherwise abort the whole assembler and
    hide the keyless Google-News media feed entirely.
    """
    try:
        return fetch(ticker, start_date, end_date)
    except Exception as exc:
        logger.warning(
            "news sub-feed %s failed for %s: %s",
            getattr(fetch, "__name__", fetch), ticker, exc,
        )
        return ""


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return EDINET + TDnet disclosures + Google-News media for ``ticker``.

    Combines whichever sub-feeds have data (statutory filings, then timely
    disclosures, then media); an empty or failed sub-feed contributes nothing.
    Raises ``NoMarketDataError`` when none has data so the router can fall through
    to yfinance.
    """
    blocks = []
    for fetch in (_edinet_news, _tdnet_news, _google_news):
        block = _safe_feed(fetch, ticker, start_date, end_date)
        if block.startswith(_DATA_PREFIX):
            blocks.append(block)

    if not blocks:
        raise NoMarketDataError(
            ticker, detail="no EDINET disclosures or media news in the window"
        )
    return "\n\n".join(blocks)
