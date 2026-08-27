"""Concurrent A-share news assembler with per-source fault isolation."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from tradingagents.provenance import (
    ProvenanceRecord,
    attach_evidence_span,
    attach_provenance,
)

from ..config import get_config
from ..errors import NoMarketDataError, VendorRateLimitError
from ..news_quality import canonical_headline
from ..rate_limit import stop_on_rate_limit_requested
from .google_news import get_news as _google_news
from .news_sources import (
    get_disclosure_news as _disclosure_news,
    get_research_news as _research_news,
)

logger = logging.getLogger(__name__)

_PARTIAL_QUERY_RE = re.compile(
    r"(?P<failed>\d+) of (?P<total>\d+) (?:Google News )?name queries failed",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _MergeCounts:
    returned: int
    duplicates: int
    kept: int
    cap_omitted: int


def _article_key(paragraph: str) -> str:
    first_line = paragraph.splitlines()[0].removeprefix("### ").strip()
    if first_line.startswith("[") and "] " in first_line:
        first_line = first_line.split("] ", 1)[1]
    for marker in (" (source:", " (institution:"):
        first_line = first_line.split(marker, 1)[0]
    return canonical_headline(first_line)


def _dedupe_blocks(
    blocks: list[str], limit: int
) -> tuple[list[str], list[_MergeCounts]]:
    """Deduplicate across sources and enforce the final article limit."""
    seen: set[str] = set()
    output = []
    counts = []
    article_count = 0
    for block in blocks:
        if article_count >= limit:
            returned = sum(
                paragraph.startswith("### ") for paragraph in block.split("\n\n")[1:]
            )
            counts.append(_MergeCounts(returned, 0, 0, returned))
            continue
        paragraphs = block.split("\n\n")
        kept = [paragraphs[0]]
        returned = 0
        duplicates = 0
        cap_omitted = 0
        for paragraph in paragraphs[1:]:
            if not paragraph.startswith("### "):
                kept.append(paragraph)
                continue
            returned += 1
            key = _article_key(paragraph)
            if not key or key in seen:
                duplicates += 1
                continue
            seen.add(key)
            if article_count < limit:
                kept.append(paragraph)
                article_count += 1
            else:
                cap_omitted += 1
        kept_count = sum(paragraph.startswith("### ") for paragraph in kept[1:])
        counts.append(
            _MergeCounts(returned, duplicates, kept_count, cap_omitted)
        )
        if len(kept) > 1:
            output.append("\n\n".join(kept))
    return output, counts


def _safe_feed(source: str, fetch, ticker: str, start_date: str, end_date: str) -> str:
    try:
        return fetch(ticker, start_date, end_date)
    except VendorRateLimitError:
        if stop_on_rate_limit_requested():
            raise
        logger.warning("CN news sub-feed %s rate-limited for %s", source, ticker)
        return f"<{source} unavailable: VendorRateLimitError>"
    except Exception as exc:  # noqa: BLE001 - each external feed is isolated
        logger.warning("CN news sub-feed %s failed for %s: %s", source, ticker, exc)
        return f"<{source} unavailable: {type(exc).__name__}>"


def _partial_query_timing(output: str) -> str | None:
    """Return an auditable status for a partially successful name-query fanout."""
    match = _PARTIAL_QUERY_RE.search(output)
    if match is None:
        return None
    return (
        "partial coverage; "
        f"query_failures={match.group('failed')}/{match.group('total')}"
    )


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Combine CNINFO, Eastmoney and Chinese Google News; fall back only if empty."""
    feeds = (
        ("CNINFO", _disclosure_news),
        ("Eastmoney Research", _research_news),
        ("Google News China", _google_news),
    )
    if stop_on_rate_limit_requested():
        rendered = [
            _safe_feed(source, fetch, ticker, start_date, end_date)
            for source, fetch in feeds
        ]
    else:
        with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
            rendered = list(
                pool.map(
                    lambda feed: _safe_feed(
                        feed[0], feed[1], ticker, start_date, end_date
                    ),
                    feeds,
                )
            )
    article_limit = max(1, int(get_config()["news_article_limit"]))
    blocks, merged_counts = _dedupe_blocks(
        [item for item in rendered if item.startswith("## ")], article_limit
    )
    notes: list[tuple[str, ProvenanceRecord]] = []
    bound_blocks: list[str] = []
    unbound_records: list[ProvenanceRecord] = []
    data_count_index = 0
    merged_index = 0
    for (source, _fetch), output in zip(feeds, rendered, strict=True):
        partial_timing = _partial_query_timing(output)
        if output.startswith("## "):
            counts = merged_counts[data_count_index]
            data_count_index += 1
            timing = (
                "publication-date filtered; "
                f"returned_items={counts.returned}; "
                f"duplicate_items={counts.duplicates}; "
                f"kept_items={counts.kept}; shared_limit={article_limit}"
            )
            if counts.cap_omitted:
                timing += f"; truncated_by_global_cap={counts.cap_omitted}"
            if partial_timing:
                timing += f"; {partial_timing}"
        elif output.startswith("<"):
            timing = partial_timing or "unavailable"
        else:
            timing = "available; no relevant items in window; returned_items=0"
        record = ProvenanceRecord(
            evidence="get_news",
            source=source,
            requested=f"{start_date} to {end_date}",
            effective=f"{start_date} to {end_date}",
            timing=timing,
        )
        if output.startswith("<"):
            notes.append((output, record))
        elif output.startswith("## ") and counts.kept:
            bound_blocks.append(
                attach_evidence_span(
                    attach_provenance(blocks[merged_index], record),
                    temporal_scope="point_in_time",
                )
            )
            merged_index += 1
        else:
            unbound_records.append(record)

    if not bound_blocks:
        raise NoMarketDataError(
            ticker,
            detail="no CNINFO announcements, Eastmoney research, or Chinese media news in the window",
            availability_notes=(
                attach_provenance(note, record) for note, record in notes
            ),
        )
    if notes:
        bound_blocks.append(
            attach_provenance(
                "### Source availability notes\n"
                + "\n".join(note for note, _record in notes),
                *(record for _note, record in notes),
            )
        )
    if unbound_records:
        bound_blocks.append(attach_provenance("", *unbound_records))
    return "\n\n".join(bound_blocks)
