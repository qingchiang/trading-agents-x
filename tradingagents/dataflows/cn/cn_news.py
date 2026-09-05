"""Concurrent A-share news assembler with per-source fault isolation."""

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
from ..news_quality import canonical_headline
from ..news_selection import candidate_scope, merge_news_blocks
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
    return merge_news_blocks(blocks, limit)


def _safe_feed(source: str, fetch, ticker: str, start_date: str, end_date: str) -> str:
    try:
        with candidate_scope():
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
                    lambda pair: pair[0].run(_safe_feed, pair[1][0], pair[1][1], ticker, start_date, end_date),
                    [(copy_context(), feed) for feed in feeds],
                )
            )
    article_limit = max(1, int(get_config()["news_article_limit"]))
    base_quotas = ((article_limit + 1) // 2, article_limit // 4,
                   article_limit - (article_limit + 1) // 2 - article_limit // 4)
    blocks, merged_counts = merge_news_blocks(
        [item for item in rendered if item.startswith("## ")], article_limit,
        start_date, end_date,
        quotas=[q for q, item in zip(base_quotas, rendered, strict=True) if item.startswith("## ")],
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
