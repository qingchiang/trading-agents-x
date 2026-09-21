"""Concurrent A-share news assembler with per-source fault isolation."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace

from tradingagents.data.cn.google_news import get_news as _google_news
from tradingagents.data.cn.news_sources import (
    get_disclosure_news as _disclosure_news,
    get_research_news as _research_news,
)
from tradingagents.data.context import DataRequestContext
from tradingagents.data.news_cache import fetch_news_feed
from tradingagents.data.news_selection import candidate_scope, merge_news_blocks, news_observations
from tradingagents.data.rate_limit import stop_on_rate_limit_requested
from tradingagents.data.result_metadata import source_metadata
from tradingagents.domain.data import ProvenanceRecord
from tradingagents.domain.data_result import DataDiagnostic, DataResult
from tradingagents.domain.vendor_errors import NoMarketDataError, VendorRateLimitError

logger = logging.getLogger(__name__)


def _safe_feed(
    source: str,
    fetch,
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    data_context: DataRequestContext,
) -> DataResult[str]:
    try:
        with candidate_scope():
            return fetch_news_feed(
                source,
                ticker,
                start_date,
                end_date,
                lambda: fetch(ticker, start_date, end_date, data_context=data_context),
                budget=data_context.config.get("cn_news_candidate_limit", 100),
                config=data_context.config,
            )
    except VendorRateLimitError:
        if stop_on_rate_limit_requested():
            raise
        logger.warning("CN news sub-feed %s rate-limited for %s", source, ticker)
        return DataResult(
            f"<{source} unavailable: VendorRateLimitError>",
            diagnostics=(DataDiagnostic("source_unavailable", source, "VendorRateLimitError"),),
        )
    except Exception as exc:  # noqa: BLE001 - each external feed is isolated
        logger.warning("CN news sub-feed %s failed for %s: %s", source, ticker, exc)
        return DataResult(
            f"<{source} unavailable: {type(exc).__name__}>",
            diagnostics=(DataDiagnostic("source_unavailable", source, type(exc).__name__),),
        )


@source_metadata("get_news", "cn_news")
def get_news(
    ticker: str, start_date: str, end_date: str, *, data_context: DataRequestContext
) -> DataResult[str]:
    """Combine CNINFO, Eastmoney and Chinese Google News; fall back only if empty."""
    feeds = (
        ("CNINFO", _disclosure_news),
        ("Eastmoney Research", _research_news),
        ("Google News China", _google_news),
    )
    if stop_on_rate_limit_requested():
        rendered = [
            _safe_feed(source, fetch, ticker, start_date, end_date, data_context=data_context)
            for source, fetch in feeds
        ]
    else:
        with ThreadPoolExecutor(max_workers=len(feeds)) as pool:
            rendered = list(
                pool.map(
                    lambda pair: pair[0].run(
                        _safe_feed,
                        pair[1][0],
                        pair[1][1],
                        ticker,
                        start_date,
                        end_date,
                        data_context=data_context,
                    ),
                    [(copy_context(), feed) for feed in feeds],
                )
            )
    article_limit = max(1, int(data_context.config["news_article_limit"]))
    base_quotas = (
        (article_limit + 1) // 2,
        article_limit // 4,
        article_limit - (article_limit + 1) // 2 - article_limit // 4,
    )
    blocks, merged_counts = merge_news_blocks(
        [item for item in rendered if item.news],
        article_limit,
        start_date,
        end_date,
        quotas=[q for q, item in zip(base_quotas, rendered, strict=True) if item.news],
    )
    notes: list[tuple[DataResult[str], ProvenanceRecord]] = []
    bound_blocks: list[DataResult[str]] = []
    unbound_records: list[ProvenanceRecord] = []
    data_count_index = 0
    merged_index = 0
    for (source, _fetch), output in zip(feeds, rendered, strict=True):
        partial_timing = next(
            (issue.detail for issue in output.diagnostics if issue.code == "query_partial"), None
        )
        if output.news:
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
        elif any(issue.code == "source_unavailable" for issue in output.diagnostics):
            timing = partial_timing or "unavailable"
        else:
            timing = "available; no relevant items in window; returned_items=0"
        filter_note = "; ".join(
            issue.detail
            for issue in output.diagnostics
            if issue.code == "candidate_filter" and issue.detail
        )
        if filter_note:
            timing += "; " + filter_note
        record = ProvenanceRecord(
            evidence="get_news",
            source=source,
            requested=f"{start_date} to {end_date}",
            effective=f"{start_date} to {end_date}",
            timing=timing,
        )
        if any(issue.code == "source_unavailable" for issue in output.diagnostics):
            notes.append((output, record))
        elif output.news and counts.kept:
            selected = replace(
                blocks[merged_index],
                observations=news_observations(blocks[merged_index].news, source, ticker),
            )
            bound_blocks.append(selected.with_provenance(record).with_scope("point_in_time"))
            merged_index += 1
        else:
            unbound_records.append(record)

    if not bound_blocks:
        raise NoMarketDataError(
            ticker,
            detail="no CNINFO announcements, Eastmoney research, or Chinese media news in the window",
            availability_notes=(
                *(note.with_provenance(record) for note, record in notes),
                *(
                    DataResult("").with_provenance(record)
                    for record in unbound_records
                    if "Candidate filter:" in record.timing
                ),
            ),
        )
    if notes:
        bound_blocks.append(
            DataResult(
                "### Source availability notes\n"
                + "\n".join((note.content for note, _record in notes))
            ).with_provenance(*(record for _note, record in notes))
        )
    if unbound_records:
        bound_blocks.append(DataResult("").with_provenance(*unbound_records))
    return DataResult.combine(bound_blocks)
