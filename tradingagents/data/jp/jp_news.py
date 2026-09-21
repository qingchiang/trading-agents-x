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
(their wall time becomes the slowest one, not the sum). Upstream requests respect
EDINET's 90-day scan limit and TDnet's 31-date free archive; Google keeps the
requested window. Previously cached candidates may supplement those source windows.
Output order (statutory → timely → media) is preserved regardless. The
assembler applies the configured ticker-news limit once, after cross-source
headline deduplication, so the three feeds share one prompt budget and official
disclosures retain priority over media when that budget is exhausted.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace

from tradingagents.data.context import DataRequestContext
from tradingagents.data.jp.edinet_common import effective_window as _edinet_effective_window
from tradingagents.data.jp.edinet_news import get_news as _edinet_news
from tradingagents.data.jp.google_news import get_news as _google_news
from tradingagents.data.jp.tdnet_news import (
    effective_window as _tdnet_effective_window,
    get_news as _tdnet_news,
)
from tradingagents.data.news_cache import fetch_news_feed
from tradingagents.data.news_selection import candidate_scope, merge_news_blocks, news_observations
from tradingagents.data.rate_limit import stop_on_rate_limit_requested
from tradingagents.data.result_metadata import source_metadata
from tradingagents.domain.data import ProvenanceRecord
from tradingagents.domain.data_result import DataDiagnostic, DataResult
from tradingagents.domain.vendor_errors import NoMarketDataError, VendorRateLimitError

logger = logging.getLogger(__name__)

# A sub-feed emits a "## …" header only when it has items (a "No … found" line
# otherwise), so this prefix tells "has data" from "empty"/failed without
# re-fetching. Kept in sync with the sub-feeds' headers by their tests.
_DATA_PREFIX = "## "
_NOTE_PREFIX = "<"


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
    data_context: DataRequestContext,
) -> DataResult[str]:
    """Run one sub-feed, degrading any failure to an availability note.

    An unguarded EDINET error (e.g. ``EDINET_API_KEY`` unset — expected on a
    keyless run — or a rate limit) would otherwise abort the whole assembler and
    hide the keyless Google-News media feed entirely.
    """
    try:
        with candidate_scope():
            return fetch_news_feed(
                source,
                ticker,
                cache_start or start_date,
                cache_end or end_date,
                lambda: fetch(ticker, start_date, end_date, data_context=data_context),
                config=data_context.config,
            )
    except VendorRateLimitError:
        if stop_on_rate_limit:
            raise
        logger.warning("news sub-feed %s rate-limited for %s", source, ticker)
        return DataResult(
            f"<{source} unavailable: VendorRateLimitError>",
            diagnostics=(DataDiagnostic("source_unavailable", source, "VendorRateLimitError"),),
        )
    except Exception as exc:
        logger.warning(
            "news sub-feed %s failed for %s: %s",
            getattr(fetch, "__name__", fetch),
            ticker,
            exc,
        )
        return DataResult(
            f"<{source} unavailable: {type(exc).__name__}>",
            diagnostics=(DataDiagnostic("source_unavailable", source, type(exc).__name__),),
        )


@source_metadata("get_news", "jp_news")
def get_news(
    ticker: str, start_date: str, end_date: str, *, data_context: DataRequestContext
) -> DataResult[str]:
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
                cache_start=start_date,
                cache_end=end_date,
                data_context=data_context,
            )
            for source, fetch, effective_start, effective_end, _effective, _limited in feed_requests
        ]
    else:
        # Fan out the independent network fetches; ``map`` yields results in
        # feed order, so the rendered blocks preserve statutory → timely → media.
        with ThreadPoolExecutor(max_workers=len(feed_requests)) as pool:
            rendered = list(
                pool.map(
                    lambda pair: pair[0].run(
                        _safe_feed,
                        pair[1][0],
                        pair[1][1],
                        ticker,
                        pair[1][2],
                        pair[1][3],
                        cache_start=start_date,
                        cache_end=end_date,
                        data_context=data_context,
                    ),
                    [(copy_context(), request) for request in feed_requests],
                )
            )
    data_blocks = [block for block in rendered if block.news]
    limit = max(1, int(data_context.config["news_article_limit"]))
    blocks, merged_counts = merge_news_blocks(data_blocks, limit, start_date, end_date)
    notes: list[tuple[DataResult[str], ProvenanceRecord]] = []
    omitted_data_records: list[ProvenanceRecord] = []
    bound_blocks: list[DataResult[str]] = []
    data_count_index = 0
    merged_index = 0
    for (source, _fetch, _effective_start, _effective_end, effective, limited), block in zip(
        feed_requests, rendered, strict=True
    ):
        if block.news:
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
        elif any(issue.code == "source_unavailable" for issue in block.diagnostics):
            timing = "unavailable"
        else:
            timing = "available; no relevant items in window; returned_items=0"
        if limited:
            timing += "; source_window_limited"
        filter_note = "; ".join(
            issue.detail
            for issue in block.diagnostics
            if issue.code == "candidate_filter" and issue.detail
        )
        if filter_note:
            timing += "; " + filter_note
        record = ProvenanceRecord(
            evidence="get_news",
            source=source,
            requested=f"{start_date} to {end_date}",
            effective=effective,
            timing=timing,
        )
        if any(issue.code == "source_unavailable" for issue in block.diagnostics):
            notes.append((block, record))
        elif block.news and counts.kept:
            selected = replace(
                blocks[merged_index],
                observations=news_observations(blocks[merged_index].news, source, ticker),
            )
            bound_blocks.append(selected.with_provenance(record).with_scope("point_in_time"))
            merged_index += 1
        elif block.news or filter_note:
            # Retain the auditable cap/duplicate disposition without binding a
            # non-rendered item to this source's evidence span.
            omitted_data_records.append(record)

    if not bound_blocks:
        raise NoMarketDataError(
            ticker,
            detail="no EDINET/TDnet disclosures or media news in the window",
            availability_notes=(
                *(note.with_provenance(record) for note, record in notes),
                *(DataResult("").with_provenance(record) for record in omitted_data_records),
            ),
        )
    if notes:
        bound_blocks.append(
            DataResult(
                "### Source availability notes\n"
                + "\n".join((note.content for note, _record in notes))
            ).with_provenance(*(record for _note, record in notes))
        )
    if omitted_data_records:
        bound_blocks.append(DataResult("").with_provenance(*omitted_data_records))
    return DataResult.combine(bound_blocks)
