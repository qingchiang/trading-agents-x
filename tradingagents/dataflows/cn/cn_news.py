"""Concurrent A-share news assembler with per-source fault isolation."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from tradingagents.provenance import ProvenanceRecord, attach_provenance

from ..config import get_config
from ..errors import NoMarketDataError
from ..news_quality import canonical_headline
from .google_news import get_news as _google_news
from .news_sources import (
    get_disclosure_news as _disclosure_news,
    get_research_news as _research_news,
)

logger = logging.getLogger(__name__)


def _article_key(paragraph: str) -> str:
    first_line = paragraph.splitlines()[0].removeprefix("### ").strip()
    if first_line.startswith("[") and "] " in first_line:
        first_line = first_line.split("] ", 1)[1]
    for marker in (" (source:", " (institution:"):
        first_line = first_line.split(marker, 1)[0]
    return canonical_headline(first_line)


def _dedupe_blocks(blocks: list[str], limit: int) -> list[str]:
    """Deduplicate across sources and enforce the final article limit."""
    seen: set[str] = set()
    output = []
    article_count = 0
    for block in blocks:
        if article_count >= limit:
            break
        paragraphs = block.split("\n\n")
        kept = [paragraphs[0]]
        for paragraph in paragraphs[1:]:
            if not paragraph.startswith("### "):
                kept.append(paragraph)
                continue
            key = _article_key(paragraph)
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(paragraph)
            article_count += 1
            if article_count >= limit:
                break
        if len(kept) > 1:
            output.append("\n\n".join(kept))
    return output


def _safe_feed(source: str, fetch, ticker: str, start_date: str, end_date: str) -> str:
    try:
        return fetch(ticker, start_date, end_date)
    except Exception as exc:  # noqa: BLE001 - each external feed is isolated
        logger.warning("CN news sub-feed %s failed for %s: %s", source, ticker, exc)
        return f"<{source} unavailable: {type(exc).__name__}>"


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Combine CNINFO, Eastmoney and Chinese Google News; fall back only if empty."""
    feeds = (
        ("CNINFO", _disclosure_news),
        ("Eastmoney Research", _research_news),
        ("Google News China", _google_news),
    )
    with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
        rendered = list(
            pool.map(
                lambda feed: _safe_feed(feed[0], feed[1], ticker, start_date, end_date),
                feeds,
            )
        )
    article_limit = max(1, int(get_config()["news_article_limit"]))
    blocks = _dedupe_blocks(
        [item for item in rendered if item.startswith("## ")], article_limit
    )
    notes = [item for item in rendered if item.startswith("<")]
    if not blocks:
        raise NoMarketDataError(
            ticker,
            detail="no CNINFO announcements, Eastmoney research, or Chinese media news in the window",
            availability_notes=notes,
        )
    if notes:
        blocks.append("### Source availability notes\n" + "\n".join(notes))
    records = []
    for (source, _fetch), output in zip(feeds, rendered, strict=True):
        if output.startswith("## "):
            timing = f"publication-date filtered; returned_items={output.count(chr(10) + '### ')}"
        elif output.startswith("<"):
            timing = "unavailable"
        else:
            timing = "available; no relevant items in window; returned_items=0"
        records.append(
            ProvenanceRecord(
                evidence="get_news",
                source=source,
                requested=f"{start_date} to {end_date}",
                effective=f"{start_date} to {end_date}",
                timing=timing,
            )
        )
    return attach_provenance("\n\n".join(blocks), *records)
