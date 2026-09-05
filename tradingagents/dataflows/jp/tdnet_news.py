"""Per-ticker Japanese timely-disclosure feed backed by TDnet (適時開示).

TDnet is the Tokyo Stock Exchange's timely-disclosure service — earnings,
guidance revisions, dividends, buybacks, M&A: the market-moving corporate events
that EDINET's statutory filings and media headlines don't front. The free
適時開示情報検索サービス exposes a keyless search
(``POST /onsf/TDJFSearch/TDJFSearch``) that filters **server-side** by code and
date range, so one request returns just this ticker's disclosures — far lighter
on TDnet than scraping the market-wide per-date list pages and filtering locally.
The service exposes the disclosure date plus the preceding 30 calendar dates
(weekends and holidays included). Requests are clamped to that rolling archive;
fully older historical windows render unavailable rather than "no disclosures".

Look-ahead safety is enforced **here**, not delegated to the server: the search
tolerates loose date args (it even accepts a reversed range), so we re-check each
row's own disclosure timestamp against ``[start_date, end_date]`` (JST calendar
days) and drop anything outside — a historical window can never admit a later
disclosure. We also re-check the securities code per row, since ``q`` is a
free-word match (code/name/title) that could in principle match another name.

Only the disclosure list (time / code / title / PDF link) is surfaced, mirroring
the EDINET feed; fetching the PDF body is a possible later enhancement. Reuses
the identified-User-Agent + 429/Retry-After backoff shape of the other stdlib
feeds (Reddit, Google News) — no new dependency.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urljoin
from urllib.request import Request

from ..config import get_config
from ..news_diagnostics import CandidateFilterCounts
from ..news_selection import source_output_limit
from ..symbol_utils import tokyo_securities_base
from .calendar import tokyo_today
from .http_util import USER_AGENT, fetch_bytes
from .jquants_common import to_jquants_code

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.release.tdnet.info/onsf/TDJFSearch/TDJFSearch"
_HOST = "https://www.release.tdnet.info"

# The search result table (``id="maintable"``) lists one disclosure per
# ``<tr class="odd|even">`` with stable per-cell classes; the title cell wraps
# the headline in an ``<a href="/inbs/…​.pdf">`` to the free official PDF. The
# result header reports the match count as ``<span id="result">N件</span>``.
# ``<tr ...>`` / ``<td ...>`` allow extra attributes (and any order) so a benign
# markup tweak doesn't silently drop every row.
_COUNT_RE = re.compile(r'id="result">\s*(\d+)\s*件')
_ROW_RE = re.compile(r'<tr\b[^>]*class="(?:odd|even)"[^>]*>(?P<row>.*?)</tr>', re.S)
_TIME_RE = re.compile(r'class="time"[^>]*>(.*?)</td>', re.S)
_CODE_RE = re.compile(r'class="code"[^>]*>(.*?)</td>', re.S)
_TITLE_CELL_RE = re.compile(r'class="title"[^>]*>(.*?)</td>', re.S)
_ANCHOR_RE = re.compile(r'href="(?P<href>[^"]*)"[^>]*>(?P<text>.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
# TDnet renders the timestamp as "YYYY/MM/DD HH:MM"; tolerate an optional :SS tail.
_TIMESTAMP_FORMATS = ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S")
_MAX_LOOKBACK_DAYS = 30


def effective_window(
    start_date: str,
    end_date: str,
    *,
    today: date | None = None,
) -> tuple[str, str, bool] | None:
    """Return the actual free-archive query window, or ``None`` if absent."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    retained_start = (today or tokyo_today()) - timedelta(days=_MAX_LOOKBACK_DAYS)
    if end < retained_start:
        return None
    effective_start = max(start, end - timedelta(days=_MAX_LOOKBACK_DAYS), retained_start)
    return (
        effective_start.strftime("%Y-%m-%d"),
        end.strftime("%Y-%m-%d"),
        effective_start != start,
    )


def _clean(text: str) -> str:
    """Strip tags, decode HTML entities, and trim surrounding whitespace.

    Titles carry entities (``M&amp;A``, ``&nbsp;``); decode after stripping tags so
    the surfaced headline reads correctly. ``str.strip()`` already drops the
    full-width ideographic space (U+3000), which TDnet pads cells with.
    """
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _search(code: str, start_compact: str, end_compact: str, timeout: float) -> str | None:
    """POST the code/date search and return the result HTML, or None on failure.

    ``t0``/``t1`` are the (older, newer) date bounds as ``YYYYMMDD``; ``m=0`` is
    the required mode field. The shared fetch backs off once on a 429 and degrades
    to None on any other network/HTTP error.
    """
    body = urlencode({"q": code, "t0": start_compact, "t1": end_compact, "m": "0"})
    req = Request(
        _SEARCH_URL,
        data=body.encode(),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    raw = fetch_bytes(req, timeout, f"TDnet {code}")
    return raw.decode("utf-8", "replace") if raw is not None else None


def _parse_rows(page_html: str, *, counts: CandidateFilterCounts | None = None) -> list[dict]:
    """Parse disclosure rows out of a TDnet search result page.

    Returns dicts with ``code`` (5-digit), ``title``, ``pdf`` (absolute URL) and
    ``at`` (the disclosure :class:`~datetime.datetime`, for both the window filter
    and recency sort). Rows missing required cells or an unparseable timestamp are
    skipped — an undated row can't be proven in-window.
    """
    counts = counts if counts is not None else CandidateFilterCounts()
    rows = []
    for block in _ROW_RE.finditer(page_html):
        counts.upstream_returned += 1
        row = block.group("row")
        time_m = _TIME_RE.search(row)
        code_m = _CODE_RE.search(row)
        title_cell = _TITLE_CELL_RE.search(row)
        if not (time_m and code_m and title_cell):
            counts.invalid_records += 1
            continue
        anchor = _ANCHOR_RE.search(title_cell.group(1))
        if not anchor:
            counts.invalid_records += 1
            continue
        title = _clean(anchor.group("text"))
        at = _parse_timestamp(_clean(time_m.group(1)))
        if at is None:
            counts.date_filtered += 1
            continue
        if not title:
            counts.invalid_records += 1
            continue
        rows.append({
            "code": _clean(code_m.group(1)),
            "title": title,
            # href is normally root-relative ("/inbs/…"); urljoin also handles an
            # absolute or bare-relative href without producing a malformed URL.
            "pdf": urljoin(_HOST, html.unescape(anchor.group("href").strip())),
            "at": at,
        })
    return rows


def _parse_timestamp(raw: str) -> datetime | None:
    """Parse a TDnet "YYYY/MM/DD HH:MM[:SS]" timestamp, or None if unrecognised."""
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def get_news(ticker: str, start_date: str, end_date: str, timeout: float = 10.0) -> str:
    """Return TDnet timely disclosures for ``ticker`` in ``[start_date, end_date]``.

    One keyless search request (server-side filtered by code and date), then a
    client-side re-check of each row's code and disclosure date for exactness and
    look-ahead safety. Returns a markdown block, or an informative "no
    disclosures" line when the company disclosed nothing in the window (a normal
    outcome — never raises, matching the other news vendors).
    """
    code = to_jquants_code(ticker)
    window = effective_window(start_date, end_date)
    if window is None:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
            retained_start = tokyo_today() - timedelta(days=_MAX_LOOKBACK_DAYS)
        except (TypeError, ValueError):
            return _no_disclosures(ticker, start_date, end_date)
        if end < retained_start:
            return (
                "<TDnet unavailable: the free service exposes only 31 calendar dates "
                f"including today; requested historical window {start_date} to {end_date} "
                "is outside the rolling archive>"
            )
        return _no_disclosures(ticker, start_date, end_date)
    # Clamp both to 31 dates ending on the requested analysis date and to what
    # remains in today's rolling free archive. Headers below use these effective
    # dates so a partial historical window is never presented as complete.
    start_date, end_date, _limited = window
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    page = _search(code, start_date.replace("-", ""), end_date.replace("-", ""), timeout)
    counts = CandidateFilterCounts()
    rows = _parse_rows(page, counts=counts) if page else []
    _warn_if_truncated(page, code, len(rows))
    # Re-check server-side filters locally: exact code (``q`` is a free-word match)
    # and the disclosure date within the window (the search accepts loose/reversed
    # ranges, so look-ahead safety must be enforced here, not trusted to it).
    matches = []
    for row in rows:
        if tokyo_securities_base(row["code"]) != code:
            counts.relevance_filtered += 1
        elif not start <= row["at"].date() <= end:
            counts.date_filtered += 1
        else:
            matches.append(row)
    if not matches:
        return _no_disclosures(ticker, start_date, end_date) + "\n" + counts.render()

    matches.sort(key=lambda r: r["at"], reverse=True)  # most recent first
    kept = matches[: source_output_limit(get_config()["news_article_limit"])]
    counts.source_truncated = len(matches) - len(kept)
    body = "\n\n".join(
        f"### {r['title']}\nDisclosed: {r['at'].strftime('%Y-%m-%d %H:%M')} JST · PDF: {r['pdf']}"
        for r in kept
    )
    return (
        f"## {ticker} timely disclosures (TDnet 適時開示), "
        f"from {start_date} to {end_date}:\n\n{counts.render()}\n\n{body}"
    )


def _no_disclosures(ticker: str, start_date: str, end_date: str) -> str:
    return f"No TDnet disclosures found for {ticker} between {start_date} and {end_date}"


def _warn_if_truncated(page_html: str | None, code: str, parsed: int) -> None:
    """Log if TDnet reports more results than the page yielded (unhandled pagination).

    Per-stock disclosure counts over ~31 days are small (single digits) and the
    search hasn't shown a pager, so this is a guard, not an expected path.
    """
    count_m = _COUNT_RE.search(page_html) if page_html else None
    if count_m and int(count_m.group(1)) > parsed:
        logger.warning(
            "TDnet reported %s results for %s but parsed %d rows — possible pagination.",
            count_m.group(1), code, parsed,
        )
