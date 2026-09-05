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

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from urllib.request import Request

from ..config import get_config
from ..news_quality import (
    build_company_aliases,
    canonical_headline,
    classify_google_article,
)
from ..news_selection import source_output_limit
from .company_info import get_company_name
from .http_util import USER_AGENT, fetch_bytes
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)

_RSS = "https://news.google.com/rss/search?{qs}"

# The feed dates in GMT but a Tokyo ticker's window is in JST calendar days;
# converting before the window filter avoids a ~9h skew that would otherwise admit
# early-next-JST-day headlines into a backtest (look-ahead safety).
_JST = timezone(timedelta(hours=9))

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
    if dt.tzinfo is None:  # RFC-822 without a zone — treat as UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_JST).replace(tzinfo=None)


def _fetch_items(query: str, timeout: float) -> list[dict]:
    """Fetch + parse the Google News JP RSS search feed for ``query``.

    Returns dicts with ``title`` (source suffix stripped), ``source``, ``pub_date``.
    Degrades to [] on any network/parse error; the shared fetch backs off once on
    a 429.
    """
    qs = urlencode({"hl": "ja", "gl": "JP", "ceid": "JP:ja", "q": query})
    req = Request(_RSS.format(qs=qs), headers={"User-Agent": USER_AGENT})
    raw = fetch_bytes(req, timeout, f"Google News {query!r}")
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        logger.warning("Google News parse failed for %r: %s", query, exc)
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

    fetched = _fetch_items(query, timeout)
    candidates = [
        it
        for it in fetched
        if it["title"]
        and _in_window(it["pub_date"], start_dt, end_dt)
    ]
    if not candidates:
        return f"No Google News found for {ticker} between {start_date} and {end_date}"
    aliases = build_company_aliases(ticker, name)
    candidates.sort(key=lambda it: it["pub_date"], reverse=True)
    relevant = []
    seen_titles: set[str] = set()
    for it in candidates:
        classification = classify_google_article(
            it["title"], it["source"], aliases
        )
        title_key = canonical_headline(it["title"])
        if classification.tier == "drop" or not title_key or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        relevant.append((it, classification.tier))

    kept = relevant[: source_output_limit(get_config()["news_article_limit"])]
    if not kept:
        return (
            f"No relevant Google News found for {ticker} between {start_date} and "
            f"{end_date} after quality filtering ({len(candidates)} in-window "
            "candidates dropped)"
        )

    direct_count = sum(tier == "direct" for _, tier in kept)
    candidate_count = sum(tier == "candidate" for _, tier in kept)
    context_count = sum(tier == "context" for _, tier in kept)
    dropped_count = len(candidates) - len(relevant)
    omitted_count = len(relevant) - len(kept)
    body = "\n".join(
        f"### [{tier}] {it['title']} (source: {it['source']})\n"
        f"{it['pub_date'].strftime('%Y-%m-%d')}\n"
        for it, tier in kept
    )
    stats = (
        f"Quality filter: upstream_returned={len(fetched)}; date_filtered={len(fetched) - len(candidates)}; candidates={len(candidates)}; relevant={len(relevant)}; "
        f"kept={len(kept)} (direct={direct_count}, candidate={candidate_count}, "
        f"context={context_count}); "
        f"dropped={dropped_count}; omitted_by_limit={omitted_count}."
    )
    return (
        f"## {ticker} News (media, Google News), from {start_date} to {end_date}:"
        f"\n\n{stats}\n\n{body}"
    )
