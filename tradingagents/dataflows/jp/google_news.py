"""Google News (Japanese) media-headline feed for Tokyo tickers.

EDINET gives statutory filings but no media reporting; this adds the journalism
side for ``.T`` names by querying Google News' free, keyless RSS search in the
Japan/Japanese edition (``hl=ja&gl=JP&ceid=JP:ja``) by company name. We surface
**headline + source + date** only: Google News RSS carries no article summary, and
its ``<link>`` is an encrypted redirect we deliberately don't resolve (a
per-article decode is fragile and rate-limit-prone, and the headline is the
signal). The query is ``"{company name} {code}"`` (e.g. ``トヨタ自動車 7203``): the
name alone is noisy for consumer megabrands (``トヨタ自動車`` pulls in the company's
baseball team, car reviews…), and the code alone is noisy the other way (``7203``
matches unrelated numbers), but *together* the code softly biases ranking toward
the financial context where it co-occurs, cutting the consumer/sports noise while
keeping real journalism. (This is a relevance bias, not an ``OR`` union, which
would re-add the code's standalone noise.)

Reuses the identified-User-Agent + 429/Retry-After backoff shape of the Reddit
RSS fetcher. Look-ahead safe: each item's ``pubDate`` filters to
``[start_date, end_date]``, so a historical window keeps only items already public
then; Google News has no deep archive, so a backtest window is naturally thin —
acceptable under the fork's live-first stance.
"""

from __future__ import annotations

import http.client
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import get_config
from .company_info import get_company_name
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)

_RSS = "https://news.google.com/rss/search?{qs}"
# Identified UA (Google serves the RSS to a plain descriptive token, like Reddit).
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"

# The feed dates in GMT but a Tokyo ticker's window is in JST calendar days;
# converting before the window filter avoids a ~9h skew that would otherwise admit
# early-next-JST-day headlines into a backtest (look-ahead safety).
_JST = timezone(timedelta(hours=9))

# Yahoo!ファイナンス quote/board/chart pages echo into the feed as "news" but carry
# no reporting. They all use the format "…（株）【CODE】：{page}", so the "】："
# separator flags them precisely — without dropping a real headline that merely
# contains a word like 決算情報, nor the 【アナリスト評価】… analyst items (no colon
# after the bracket) or the 日経 "[CODE]：" disclosure mirrors (ASCII brackets).
_BOILERPLATE_MARKER = "】："


def _retry_after_seconds(exc: HTTPError) -> float | None:
    """Seconds to wait from a 429 ``Retry-After`` header, capped at 30s."""
    try:
        val = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
        return min(float(val), 30.0) if val else None
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_pubdate(raw: str | None) -> datetime | None:
    """Parse an RFC-822 ``pubDate`` to a naive **JST** datetime, or None.

    The feed dates in GMT; we convert to JST (the ticker's market day) before
    dropping the tzinfo so the window filter compares like-for-like.
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:  # RFC-822 without a zone — treat as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_JST).replace(tzinfo=None)


def _fetch_items(query: str, timeout: float, _retry: bool = True) -> list[dict]:
    """Fetch + parse the Google News JP RSS search feed for ``query``.

    Returns dicts with ``title`` (source suffix stripped), ``source``, ``pub_date``.
    Degrades to [] on any network/parse error; backs off once on a 429.
    """
    qs = urlencode({"hl": "ja", "gl": "JP", "ceid": "JP:ja", "q": query})
    req = Request(_RSS.format(qs=qs), headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
    except HTTPError as exc:
        if exc.code == 429 and _retry:
            wait = _retry_after_seconds(exc) or 5.0
            logger.warning(
                "Google News 429 for %r — backing off %.1fs then retrying once", query, wait
            )
            time.sleep(wait)
            return _fetch_items(query, timeout, _retry=False)
        logger.warning("Google News fetch failed for %r: %s", query, exc)
        return []
    except (OSError, http.client.HTTPException, ET.ParseError) as exc:
        logger.warning("Google News fetch failed for %r: %s", query, exc)
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        src_el = item.find("source")
        source = ((src_el.text if src_el is not None else "") or "").strip()
        # Google appends " - {source}" to the title; drop it (source is its own field).
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
        items.append({
            "title": title,
            "source": source or "Unknown",
            "pub_date": _parse_pubdate(item.findtext("pubDate")),
        })
    return items


def _in_window(pub_date, start_dt, end_dt) -> bool:
    """Keep dated items inside the JST calendar days [start_date, end_date].

    ``pub_date`` is naive JST; ``end_dt + 1 day`` with a strict ``<`` includes all
    of ``end_date`` (up to 23:59 JST) and excludes the next JST day, so a backtest
    never admits a following-day headline. Undated items are dropped — we can't
    prove they aren't future.
    """
    if pub_date is None:
        return False
    return start_dt <= pub_date < end_dt + timedelta(days=1)


def _is_boilerplate(title: str) -> bool:
    return _BOILERPLATE_MARKER in title


def get_news(ticker: str, start_date: str, end_date: str, timeout: float = 10.0) -> str:
    """Return Google-News media headlines for a Tokyo ticker in ``[start, end]``.

    Searches by resolved company name (falls back to the bare code). Returns a
    markdown block, or a "No Google News found" line when nothing matches (never
    raises — matches the other news vendors' string contract).
    """
    # "{name} {code}" softly biases ranking to the financial context (see module
    # docstring); fall back to the bare code if the name can't be resolved.
    code = to_jquants_code(ticker)
    name = get_company_name(ticker)
    query = f"{name} {code}" if name else code

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return f"No Google News found for {ticker} between {start_date} and {end_date}"

    candidates = [
        it
        for it in _fetch_items(query, timeout)
        if it["title"]
        and _in_window(it["pub_date"], start_dt, end_dt)
        and not _is_boilerplate(it["title"])
    ]
    # Most recent first, then dedupe repeated headlines (same event, many outlets).
    candidates.sort(key=lambda it: it["pub_date"], reverse=True)
    kept, seen = [], set()
    for it in candidates:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        kept.append(it)

    kept = kept[: get_config()["news_article_limit"]]
    if not kept:
        return f"No Google News found for {ticker} between {start_date} and {end_date}"

    body = "\n".join(
        f"### {it['title']} (source: {it['source']})\n{it['pub_date'].strftime('%Y-%m-%d')}\n"
        for it in kept
    )
    return f"## {ticker} News (media, Google News), from {start_date} to {end_date}:\n\n{body}"
