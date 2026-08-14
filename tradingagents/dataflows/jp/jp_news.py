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
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradingagents.provenance import (
    ProvenanceRecord,
    SourceInterval,
    SourceWatermark,
    attach_evidence_span,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_provenance,
    extract_source_observations,
    extract_source_watermarks,
    strip_provenance_markers,
)

from ..config import get_config
from ..errors import NoMarketDataError
from ..news_quality import canonical_headline
from .edinet_news import get_news as _edinet_news
from .google_news import get_news as _google_news
from .tdnet_news import get_news as _tdnet_news

logger = logging.getLogger(__name__)

# A sub-feed emits a "## …" header only when it has items (a "No … found" line
# otherwise), so this prefix tells "has data" from "empty"/failed without
# re-fetching. Kept in sync with the sub-feeds' headers by their tests.
_DATA_PREFIX = "## "
_NOTE_PREFIX = "<"

# The free TDnet search exposes the disclosure date plus the preceding 30
# calendar dates. Other JP feeds may receive the full 90-date graph window.
_TDNET_MAX_LOOKBACK_DAYS = 30
_EDINET_OVERLAP_DAYS = 89

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


def _merge_blocks(
    blocks: list[str],
    limit: int,
) -> tuple[list[str | None], list[_MergeCounts]]:
    """Deduplicate and cap blocks in source-priority order.

    Returns rendered non-empty blocks plus disposition counts aligned with the
    input. EDINET and TDnet precede Google News at the caller, so an
    equivalent official disclosure wins over a media headline and remaining
    prompt capacity is assigned to official sources first.
    """
    remaining = max(1, limit)
    seen: set[str] = set()
    merged: list[str | None] = []
    counts: list[_MergeCounts] = []
    for block in blocks:
        parsed = _split_feed_block(block)
        kept: list[str] = []
        duplicates = 0
        cap_omitted = 0
        for item in parsed.items:
            key = _item_key(item)
            if not key:
                continue
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            if remaining > 0:
                kept.append(item)
                remaining -= 1
            else:
                cap_omitted += 1
        counts.append(
            _MergeCounts(
                returned=len(parsed.items),
                duplicates=duplicates,
                kept=len(kept),
                cap_omitted=cap_omitted,
            )
        )
        merged.append(
            f"{parsed.preamble}\n\n" + "\n\n".join(kept)
            if kept
            else None
        )
    return merged, counts


def _safe_feed(
    source: str,
    fetch,
    ticker: str,
    start_date: str,
    end_date: str,
    information_frontier: str | None,
) -> str:
    """Run one sub-feed, degrading any failure to an availability note.

    An unguarded EDINET error (e.g. ``EDINET_API_KEY`` unset — expected on a
    keyless run — or a rate limit) would otherwise abort the whole assembler and
    hide the keyless Google-News media feed entirely.
    """
    try:
        if source in {"EDINET", "TDnet"} and information_frontier is not None:
            return fetch(
                ticker,
                start_date,
                end_date,
                information_frontier=information_frontier,
            )
        return fetch(ticker, start_date, end_date)
    except Exception as exc:
        logger.warning(
            "news sub-feed %s failed for %s: %s",
            getattr(fetch, "__name__", fetch),
            ticker,
            exc,
        )
        return f"<{source} unavailable: {type(exc).__name__}>"


def _tdnet_start_date(start_date: str, end_date: str) -> str:
    """Clamp a requested range to TDnet's 31-inclusive-calendar-date limit."""
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return start_date
    return (end - timedelta(days=_TDNET_MAX_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def _edinet_start_date(start_date: str, end_date: str) -> str:
    """Always inspect EDINET's full bounded overlap for late corrections."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return start_date
    return min(start, end - timedelta(days=_EDINET_OVERLAP_DAYS)).strftime("%Y-%m-%d")


def get_news(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    information_frontier: str | None = None,
) -> str:
    """Return EDINET + TDnet disclosures + Google-News media for ``ticker``.

    Combines whichever sub-feeds have data (statutory filings, then timely
    disclosures, then media); an empty sub-feed contributes nothing, while a
    failed/unavailable source contributes an explicit note. Raises
    ``NoMarketDataError`` when none has data so the router can fall through to
    yfinance without losing those notes.
    """
    # Sub-feeds in output order (statutory → timely → media); resolved here (not
    # module scope) so tests patching these names take effect.
    feed_requests = (
        ("EDINET", _edinet_news, _edinet_start_date(start_date, end_date), end_date),
        ("TDnet", _tdnet_news, _tdnet_start_date(start_date, end_date), end_date),
        ("Google News", _google_news, start_date, end_date),
    )
    # Fan out the independent network fetches; ``map`` yields results in feed
    # order, so the rendered blocks keep that statutory → timely → media order.
    with ThreadPoolExecutor(max_workers=len(feed_requests)) as pool:
        rendered = pool.map(
            lambda request: _safe_feed(
                request[0],
                request[1],
                ticker,
                request[2],
                request[3],
                information_frontier,
            ),
            feed_requests,
        )
    rendered = list(rendered)
    observations_by_feed = tuple(
        tuple(extract_source_observations(block)) for block in rendered
    )
    watermarks_by_feed = tuple(
        tuple(extract_source_watermarks(block)) for block in rendered
    )
    provenance_by_feed = tuple(tuple(extract_provenance(block)) for block in rendered)
    rendered = [strip_provenance_markers(block) for block in rendered]
    data_blocks = [block for block in rendered if block.startswith(_DATA_PREFIX)]
    limit = max(1, int(get_config()["news_article_limit"]))
    merged_blocks, merged_counts = _merge_blocks(data_blocks, limit)
    channel_spans: list[str] = []
    data_count_index = 0
    for source_index, (
        (source, _fetch, effective_start, effective_end),
        block,
    ) in enumerate(zip(feed_requests, rendered, strict=True)):
        counts = None
        selected_body = ""
        if block.startswith(_DATA_PREFIX):
            counts = merged_counts[data_count_index]
            selected_body = merged_blocks[data_count_index] or ""
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
        record = ProvenanceRecord(
            evidence="get_news",
            source=source,
            requested=f"{start_date} to {end_date}",
            effective=f"{effective_start} to {effective_end}",
            timing=timing,
            retrieved_at=(
                _producer_retrieval_time(provenance_by_feed[source_index], source)
                if source == "Google News"
                else None
            ),
        )
        source_watermarks = tuple(
            item
            for item in watermarks_by_feed[source_index]
            if item.source == source
        )
        if not source_watermarks:
            returned = counts.returned if counts is not None else 0
            source_watermarks = (
                SourceWatermark(
                    source=source,
                    scanned_start=effective_start,
                    scanned_end=effective_end,
                    status="unavailable" if block.startswith(_NOTE_PREFIX) else "complete",
                    temporal_scope=(
                        "live_only" if source == "Google News" else "point_in_time"
                    ),
                    limitations=(
                        (f"{source} collection was unavailable.",)
                        if block.startswith(_NOTE_PREFIX)
                        else ()
                    ),
                    returned_records=returned,
                    reported_records=returned,
                    requested_interval=SourceInterval(start=start_date, end=end_date),
                    limitation_kind=(
                        "live_only"
                        if source == "Google News"
                        else "unavailable"
                        if block.startswith(_NOTE_PREFIX)
                        else None
                    ),
                ),
            )
        source_observations = tuple(
            item
            for item in observations_by_feed[source_index]
            if item.source == source
        )
        if block.startswith(_NOTE_PREFIX):
            selected_body = block
        scoped = attach_provenance(selected_body, record)
        scoped = attach_source_observations(scoped, *source_observations)
        scoped = attach_source_watermarks(scoped, *source_watermarks)
        channel_spans.append(
            attach_evidence_span(
                scoped,
                temporal_scope=(
                    "live_only" if source == "Google News" else "point_in_time"
                ),
            )
        )

    if not any(merged_blocks):
        raise NoMarketDataError(
            ticker,
            detail="no EDINET/TDnet disclosures or media news in the window",
            availability_notes=tuple(channel_spans),
        )
    return "\n\n".join(channel_spans)


def _producer_retrieval_time(
    records: tuple[ProvenanceRecord, ...],
    source: str,
) -> str | None:
    """Forward one unambiguous producer timestamp without restamping it."""

    retrieved = {
        record.retrieved_at
        for record in records
        if record.source == source and record.retrieved_at is not None
    }
    return retrieved.pop() if len(retrieved) == 1 else None
