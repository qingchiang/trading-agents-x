"""Reddit search fetcher for ticker-specific discussion posts.

Default path is Reddit's public Atom/RSS search feed
(``reddit.com/r/{sub}/search.rss``). The richer JSON search endpoint
(``/search.json``) is reliably WAF-blocked (``HTTP 403``) for public clients
(issue #862), and probing it on every call only doubled our request volume
against Reddit's per-IP rate limit — tripping ``429`` on the RSS fallback — so
it is kept (``_fetch_subreddit_json``) but not used by default. On a 429 we back
off once (honouring ``Retry-After``). RSS lacks score / comment counts, so those
posts are marked and the formatter omits the metrics rather than printing fake
zeros.

No API key required. Returns formatted plaintext blocks ready for prompt
injection and degrades gracefully — returns a placeholder string rather than
raising, so callers never special-case missing data.
"""

from __future__ import annotations

import html
import http.client
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from tradingagents.version import IDENTIFIED_USER_AGENT

from .symbol_utils import crypto_base

logger = logging.getLogger(__name__)

_API = "https://www.reddit.com/r/{sub}/search.json?{qs}"
_RSS = "https://www.reddit.com/r/{sub}/search.rss?{qs}"
# A descriptive, identified User-Agent (per Reddit's API etiquette). Reddit
# blocks generic/anonymous tokens like bare "Mozilla/5.0" or "curl/…" but
# serves this one on both endpoints; the RSS feed accepts it even when the
# JSON search endpoint 403s, so no browser-spoofing is needed.
_UA = IDENTIFIED_USER_AGENT
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Default subreddits ordered roughly by signal density for ticker-specific
# discussion. wallstreetbets has the most volume but most noise; stocks /
# investing trend more measured. Caller can override.
DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")


def _search_qs(ticker: str, limit: int, time_filter: str = "week") -> str:
    return urlencode({
        "q": ticker,
        "restrict_sr": "on",
        "sort": "new",
        "t": time_filter,
        "limit": limit,
    })


def _search_time_filter(
    start_date: str,
    end_date: str,
    *,
    today: date | None = None,
) -> str | None:
    """Choose the smallest Reddit bucket that reaches the requested window.

    Reddit's ``t=day/week/month/year`` buckets are relative to retrieval time,
    not to ``end_date``.  Account for both the requested span and the age of its
    oldest day; otherwise a narrow near-historical window (for example, one day
    ending two days ago) would incorrectly use ``day`` and never fetch any
    candidates from that window.  The final exact calendar filter still runs
    after retrieval.
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    window_span = (end - start).days
    if window_span < 0:
        return None
    if today is None:
        today = datetime.now(timezone.utc).date()
    coverage_days = max(window_span, (today - start).days)
    if coverage_days <= 0:
        return "day"
    if coverage_days <= 6:
        return "week"
    if coverage_days <= 29:
        return "month"
    if coverage_days <= 364:
        return "year"
    return "all"


def _iso_to_timestamp(iso_str: str | None) -> float | None:
    """Parse an Atom ``published`` timestamp to a UTC epoch, or None."""
    if not iso_str:
        return None
    try:
        normalized = iso_str[:-1] + "+00:00" if iso_str.endswith("Z") else iso_str
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return None


def _strip_html(content: str) -> str:
    """Reduce the HTML body Reddit embeds in an Atom entry to plain text."""
    if not content:
        return ""
    # Reddit wraps the real selftext between SC_OFF / SC_ON markers.
    if "<!-- SC_OFF -->" in content and "<!-- SC_ON -->" in content:
        content = content.split("<!-- SC_OFF -->")[1].split("<!-- SC_ON -->")[0]
    text = re.sub(r"<[^>]+>", " ", content)
    return " ".join(html.unescape(text).split())


def _retry_after_seconds(exc: HTTPError) -> float | None:
    """Seconds to wait from a 429's ``Retry-After`` header, capped at 30s."""
    try:
        val = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
        return min(float(val), 30.0) if val else None
    except (ValueError, TypeError, AttributeError):
        return None


def _fetch_subreddit_rss(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
    _retry: bool = True,
    time_filter: str = "week",
) -> list[dict] | None:
    """Default path: parse the public Atom search feed for a subreddit.

    Carries no score / comment counts, so those fields are left None and the
    post is tagged ``source="rss"`` for honest display. On a 429 (Reddit's
    per-IP rate limit) we back off once — honouring ``Retry-After`` when
    present — before giving up, so a transient burst doesn't blank the feed.
    """
    url = _RSS.format(sub=sub, qs=_search_qs(ticker, limit, time_filter))
    req = Request(url, headers={"User-Agent": _UA})
    try:
        with urlopen(req, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
    except HTTPError as exc:
        if exc.code == 429 and _retry:
            wait = _retry_after_seconds(exc) or 5.0
            logger.warning(
                "Reddit RSS 429 for r/%s · %s — backing off %.1fs then retrying once",
                sub, ticker, wait,
            )
            time.sleep(wait)
            return _fetch_subreddit_rss(
                ticker,
                sub,
                limit,
                timeout,
                _retry=False,
                time_filter=time_filter,
            )
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return None
    except (OSError, http.client.HTTPException, ET.ParseError) as exc:
        # OSError covers URLError/TimeoutError/connection resets; HTTPException
        # covers chunked-transfer errors (IncompleteRead/BadStatusLine, #1024).
        logger.warning("Reddit RSS fetch failed for r/%s · %s: %s", sub, ticker, exc)
        return None

    posts = []
    for entry in root.findall("atom:entry", _ATOM_NS)[:limit]:
        title_el = entry.find("atom:title", _ATOM_NS)
        published_el = entry.find("atom:published", _ATOM_NS)
        content_el = entry.find("atom:content", _ATOM_NS)
        posts.append({
            "title": (title_el.text if title_el is not None else "") or "",
            "score": None,
            "num_comments": None,
            "created_utc": _iso_to_timestamp(
                published_el.text if published_el is not None else None
            ),
            "selftext": _strip_html(content_el.text if content_el is not None else ""),
            "source": "rss",
        })
    return posts


def _fetch_subreddit_json(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
    time_filter: str = "week",
) -> list[dict] | None:
    """Richer JSON search path (carries score / comment counts).

    Reddit's WAF currently returns ``403 Blocked`` on this endpoint for
    non-OAuth clients (issue #862), so it is NOT used by default — calling it on
    every request only doubled our volume against the per-IP rate limit and
    triggered 429s on the RSS fallback. Kept for the day the WAF relaxes or an
    OAuth token is wired in; degrades to RSS on failure.
    """
    url = _API.format(sub=sub, qs=_search_qs(ticker, limit, time_filter))
    req = Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
        children = (payload.get("data") or {}).get("children") or []
        return [c.get("data", {}) for c in children if isinstance(c, dict)]
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
        logger.warning(
            "Reddit JSON fetch failed for r/%s · %s: %s — falling back to RSS feed.",
            sub, ticker, exc,
        )
        return _fetch_subreddit_rss(
            ticker,
            sub,
            limit,
            timeout,
            time_filter=time_filter,
        )


def _fetch_subreddit(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
    time_filter: str = "week",
) -> list[dict] | None:
    """Fetch one subreddit, RSS-first.

    The JSON search endpoint is reliably WAF-blocked (403) for public clients,
    so we go straight to the RSS feed — which serves our identified User-Agent
    reliably — halving our request volume against Reddit's per-IP rate limit.
    """
    return _fetch_subreddit_rss(
        ticker,
        sub,
        limit,
        timeout,
        time_filter=time_filter,
    )


def fetch_reddit_posts(
    ticker: str,
    subreddits: Iterable[str] = DEFAULT_SUBREDDITS,
    limit_per_sub: int = 5,
    timeout: float = 10.0,
    inter_request_delay: float = 1.0,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Fetch recent Reddit posts mentioning ``ticker`` across finance
    subreddits and return them as a formatted plaintext block.

    ``inter_request_delay`` paces the (now RSS-only) per-subreddit requests to
    stay under Reddit's public per-IP rate limit; combined with the RSS-first
    path it makes 429s rare even when several analyses run back-to-back.
    """
    # Crypto reaches us as a Yahoo pair (BTC-USD); search Reddit for the base
    # ("BTC") so the query actually matches discussion instead of near-nothing.
    is_crypto = crypto_base(ticker) is not None
    ticker = crypto_base(ticker) or ticker
    if (start_date is None) != (end_date is None):
        return "<reddit unavailable: both start_date and end_date are required>"
    time_filter = "week"
    if start_date is not None and end_date is not None:
        time_filter = _search_time_filter(start_date, end_date) or ""
        if not time_filter:
            return "<reddit unavailable: invalid start_date/end_date window>"
    fetch_kwargs = {"time_filter": time_filter} if start_date is not None else {}
    blocks = []
    for i, sub in enumerate(subreddits):
        if i > 0:
            time.sleep(inter_request_delay)
        posts = _fetch_subreddit(
            ticker,
            sub,
            limit_per_sub,
            timeout,
            **fetch_kwargs,
        )
        if posts is None:
            blocks.append(f"r/{sub}: <unavailable: Reddit feed request failed>")
            continue
        if start_date is not None and end_date is not None:
            posts = [
                post
                for post in posts
                if _post_in_window(
                    post.get("created_utc"),
                    is_crypto,
                    start_date,
                    end_date,
                )
            ]
        if not posts:
            if start_date is not None:
                blocks.append(
                    f"r/{sub}: <no posts found mentioning {ticker.upper()} in requested "
                    f"window {start_date}..{end_date} among the current public feed; "
                    "this is not evidence of no historical discussion>"
                )
            else:
                blocks.append(
                    f"r/{sub}: <no posts found mentioning {ticker.upper()} in the past 7 days>"
                )
            continue

        via_rss = any(p.get("source") == "rss" for p in posts)
        header = f"r/{sub} — {len(posts)} recent posts mentioning {ticker.upper()}"
        header += " (via RSS feed; scores/comments unavailable):" if via_rss else ":"
        lines = [header]
        for p in posts:
            title = (p.get("title") or "").replace("\n", " ").strip()
            score = p.get("score")
            comments = p.get("num_comments")
            created = p.get("created_utc")
            if created is None:
                created_str = "?"
            elif start_date is not None:
                local = _post_market_datetime(created, is_crypto)
                created_str = local.strftime("%Y-%m-%d") if local else "?"
            else:
                created_str = time.strftime("%Y-%m-%d", time.gmtime(created))
            # Score / comment counts are absent on the RSS fallback path —
            # show them only when present rather than printing fake zeros.
            meta = created_str
            if score is not None and comments is not None:
                meta += f" · {score:>4}↑ · {comments:>3}c"
            selftext = (p.get("selftext") or "").replace("\n", " ").strip()
            if len(selftext) > 240:
                selftext = selftext[:240] + "…"
            lines.append(
                f"  [{meta}] {title}"
                + (f"\n    body excerpt: {selftext}" if selftext else "")
            )
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _post_in_window(
    created_utc: float | None,
    is_crypto: bool,
    start_date: str,
    end_date: str,
) -> bool:
    """Whether a Reddit epoch timestamp falls in the target market-date window."""
    if created_utc is None:
        return False
    try:
        local = _post_market_datetime(created_utc, is_crypto)
        if local is None:
            return False
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (OSError, TypeError, ValueError):
        return False
    return start <= local.date() <= end


def _post_market_datetime(
    created_utc: float,
    is_crypto: bool,
) -> datetime | None:
    """Convert a Reddit epoch to the asset's market timezone."""
    try:
        market_tz = timezone.utc if is_crypto else ZoneInfo("America/New_York")
        return datetime.fromtimestamp(created_utc, tz=timezone.utc).astimezone(market_tz)
    except (OSError, TypeError, ValueError):
        return None
