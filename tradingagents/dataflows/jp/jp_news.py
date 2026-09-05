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

The three sub-feeds are independent blocking calls, so we fetch them concurrently
(their wall time becomes the slowest one, not the sum). Extended requests keep
the full range for EDINET/Google but clamp TDnet to its 31-date free archive.
Output order (statutory → timely → media) is preserved regardless. The
assembler applies the configured ticker-news limit once, after cross-source
headline deduplication, so the three feeds share one prompt budget and official
disclosures retain priority over media when that budget is exhausted.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass

from tradingagents.provenance import (
    ProvenanceRecord,
    attach_evidence_span,
    attach_provenance,
)

from ..config import get_config
from ..errors import NoMarketDataError, VendorRateLimitError
from ..news_cache import fetch_news_feed
from ..news_diagnostics import candidate_filter_note
from ..news_quality import canonical_headline
from ..news_selection import candidate_scope, emit_news, merge_news_blocks
from ..rate_limit import stop_on_rate_limit_requested
from .edinet_common import effective_window as _edinet_effective_window
from .edinet_news import get_news as _edinet_news
from .google_news import get_news as _google_news
from .tdnet_news import (
    effective_window as _tdnet_effective_window,
    get_news as _tdnet_news,
)

logger = logging.getLogger(__name__)

# A sub-feed emits a "## …" header only when it has items (a "No … found" line
# otherwise), so this prefix tells "has data" from "empty"/failed without
# re-fetching. Kept in sync with the sub-feeds' headers by their tests.
_DATA_PREFIX = "## "
_NOTE_PREFIX = "<"

_ITEM_START_RE = re.compile(r"(?m)^### ")
_TIER_PREFIX_RE = re.compile(r"^\[(?:direct|candidate|context)\]\s*", re.I)
_ITEM_METADATA_RE = re.compile(r"\s+\((?:source|filer):[^)]*\)\s*$", re.I)


@dataclass(frozen=True)
class _FeedBlock:
    """One rendered sub-feed split into its preamble and news items."""

    preamble: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class _MergeCounts:
    """Auditable disposition of items received from one sub-feed."""

    returned: int
    duplicates: int
    kept: int
    cap_omitted: int


def _split_feed_block(block: str) -> _FeedBlock:
    """Split a sub-feed block without depending on its source-specific body."""
    starts = [match.start() for match in _ITEM_START_RE.finditer(block)]
    if not starts:
        return _FeedBlock(block.rstrip(), ())
    preamble = block[: starts[0]].rstrip()
    items = tuple(
        block[start : starts[index + 1] if index + 1 < len(starts) else None].rstrip()
        for index, start in enumerate(starts)
    )
    return _FeedBlock(preamble, items)


def _item_key(item: str) -> str:
    """Return a source-neutral normalized key for an item's Markdown title."""
    title = item.splitlines()[0].removeprefix("### ").strip()
    title = _TIER_PREFIX_RE.sub("", title)
    title = _ITEM_METADATA_RE.sub("", title)
    return canonical_headline(title)


def _merge_blocks(blocks: list[str], limit: int) -> tuple[list[str], list[_MergeCounts]]:
    """Deduplicate and cap blocks in source-priority order.

    Returns rendered non-empty blocks plus disposition counts aligned with the
    input. EDINET and TDnet precede Google News at the caller, so an
    equivalent official disclosure wins over a media headline and remaining
    prompt capacity is assigned to official sources first.
    """
    return merge_news_blocks(blocks, limit)


def _safe_feed(
    source: str,
    fetch,
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    stop_on_rate_limit: bool = False,
    cache_start: str | None = None,
    cache_end: str | None = None,
) -> str:
    """Run one sub-feed, degrading any failure to an availability note.

    An unguarded EDINET error (e.g. ``EDINET_API_KEY`` unset — expected on a
    keyless run — or a rate limit) would otherwise abort the whole assembler and
    hide the keyless Google-News media feed entirely.
    """
    try:
        with candidate_scope():
            return fetch_news_feed(source, ticker, cache_start or start_date, cache_end or end_date, lambda: fetch(ticker, start_date, end_date), config=get_config())
    except VendorRateLimitError:
        if stop_on_rate_limit:
            raise
        logger.warning("news sub-feed %s rate-limited for %s", source, ticker)
        return f"<{source} unavailable: VendorRateLimitError>"
    except Exception as exc:
        logger.warning(
            "news sub-feed %s failed for %s: %s",
            getattr(fetch, "__name__", fetch),
            ticker,
            exc,
        )
        return f"<{source} unavailable: {type(exc).__name__}>"


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return EDINET + TDnet disclosures + Google-News media for ``ticker``.

    Combines whichever sub-feeds have data (statutory filings, then timely
    disclosures, then media); an empty sub-feed contributes nothing, while a
    failed/unavailable source contributes an explicit note. Raises
    ``NoMarketDataError`` when none has data so the router can fall through to
    yfinance without losing those notes.
    """
    # Sub-feeds in output order (statutory → timely → media); resolved here (not
    # module scope) so tests patching these names take effect.
    edinet_start, edinet_end, edinet_limited = _edinet_effective_window(start_date, end_date)
    tdnet_window = _tdnet_effective_window(start_date, end_date)
    if tdnet_window is None:
        tdnet_start, tdnet_end, tdnet_limited = start_date, end_date, True
        tdnet_effective = "outside rolling TDnet archive; no query"
    else:
        tdnet_start, tdnet_end, tdnet_limited = tdnet_window
        tdnet_effective = f"{tdnet_start} to {tdnet_end}"
    feed_requests = (
        (
            "EDINET",
            _edinet_news,
            edinet_start,
            edinet_end,
            f"{edinet_start} to {edinet_end}",
            edinet_limited,
        ),
        ("TDnet", _tdnet_news, tdnet_start, tdnet_end, tdnet_effective, tdnet_limited),
        (
            "Google News",
            _google_news,
            start_date,
            end_date,
            f"{start_date} to {end_date}",
            False,
        ),
    )
    # ContextVars do not cross the worker boundary. Read the bounded-route
    # decision here; that scope must stop after a 429, so execute in order and
    # re-raise the typed error before later feeds can begin. Full requests keep
    # the normal concurrent, best-effort composition behavior.
    scoped_stop = stop_on_rate_limit_requested()
    if scoped_stop:
        rendered = [
            _safe_feed(
                source,
                fetch,
                ticker,
                effective_start,
                effective_end,
                stop_on_rate_limit=True,
                cache_start=start_date, cache_end=end_date,
            )
            for source, fetch, effective_start, effective_end, _effective, _limited in feed_requests
        ]
    else:
        # Fan out the independent network fetches; ``map`` yields results in
        # feed order, so the rendered blocks preserve statutory → timely → media.
        with ThreadPoolExecutor(max_workers=len(feed_requests)) as pool:
            rendered = list(
                pool.map(
                    lambda pair: pair[0].run(_safe_feed, pair[1][0], pair[1][1], ticker, pair[1][2], pair[1][3], cache_start=start_date, cache_end=end_date),
                    [(copy_context(), request) for request in feed_requests],
                )
            )
    data_blocks = [block for block in rendered if block.startswith(_DATA_PREFIX)]
    limit = max(1, int(get_config()["news_article_limit"]))
    blocks, merged_counts = merge_news_blocks(data_blocks, limit, start_date, end_date)
    notes: list[tuple[str, ProvenanceRecord]] = []
    omitted_data_records: list[ProvenanceRecord] = []
    bound_blocks: list[str] = []
    data_count_index = 0
    merged_index = 0
    for (source, _fetch, _effective_start, _effective_end, effective, limited), block in zip(
        feed_requests, rendered, strict=True
    ):
        if block.startswith(_DATA_PREFIX):
            counts = merged_counts[data_count_index]
            data_count_index += 1
            timing = (
                "publication/disclosure-date filtered; "
                f"returned_items={counts.returned}; "
                f"duplicate_items={counts.duplicates}; "
                f"kept_items={counts.kept}; "
                f"shared_limit={limit}"
            )
            if counts.cap_omitted:
                timing += f"; truncated_by_global_cap={counts.cap_omitted}"
        elif block.startswith(_NOTE_PREFIX):
            timing = "unavailable"
        else:
            timing = "available; no relevant items in window; returned_items=0"
        if limited:
            timing += "; source_window_limited"
        filter_note = candidate_filter_note(block)
        if filter_note:
            timing += "; " + filter_note
        record = ProvenanceRecord(
            evidence="get_news",
            source=source,
            requested=f"{start_date} to {end_date}",
            effective=effective,
            timing=timing,
        )
        if block.startswith(_NOTE_PREFIX):
            notes.append((block, record))
        elif block.startswith(_DATA_PREFIX) and counts.kept:
            emit_news(blocks[merged_index], source, ticker)
            bound_blocks.append(
                attach_evidence_span(
                    attach_provenance(blocks[merged_index], record),
                    temporal_scope="point_in_time",
                )
            )
            merged_index += 1
        elif block.startswith(_DATA_PREFIX) or filter_note:
            # Retain the auditable cap/duplicate disposition without binding a
            # non-rendered item to this source's evidence span.
            omitted_data_records.append(record)

    if not bound_blocks:
        raise NoMarketDataError(
            ticker,
            detail="no EDINET/TDnet disclosures or media news in the window",
            availability_notes=(
                *(attach_provenance(note, record) for note, record in notes),
                *(attach_provenance("", record) for record in omitted_data_records),
            ),
        )
    if notes:
        bound_blocks.append(
            attach_provenance(
                "### Source availability notes\n" + "\n".join(note for note, _record in notes),
                *(record for _note, record in notes),
            )
        )
    if omitted_data_records:
        bound_blocks.append(attach_provenance("", *omitted_data_records))
    return "\n\n".join(bound_blocks)
