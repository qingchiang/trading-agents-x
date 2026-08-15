"""Deterministic source contracts for the sentiment analyst."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from tradingagents.agents.utils.agent_states import (
    PrefetchedEvidenceBlock,
    prefetched_evidence_block,
)
from tradingagents.application.evidence_workset import StructuredNumericFact
from tradingagents.dataflows.market_context import market_suffix_of
from tradingagents.dataflows.market_signals import FetchedSentimentSignal
from tradingagents.provenance import ProvenanceRecord, extract_provenance


class SentimentSourceStatus(str, Enum):
    """Whether one applicable source contains a substantive signal."""

    SUBSTANTIVE = "substantive"
    NO_SIGNAL = "no_signal"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SentimentConfidence:
    """Deterministic confidence derived from applicable source coverage."""

    level: Literal["low", "medium", "high"]
    score: Literal[0.25, 0.55, 0.8]


@dataclass(frozen=True)
class SentimentSourceInput:
    """One locally identified source presented to the sentiment model."""

    source_id: str
    label: str
    status: SentimentSourceStatus
    applicable: bool
    degraded: bool


_UNAVAILABLE_TIMING_TOKENS = (
    "unavailable",
    "failed",
    "not queried",
    "no usable data",
)
_DEGRADED_TIMING_TOKENS = (
    "fallback",
    "partial",
    "degraded",
    "no auditable source metadata",
    "retrieval success unknown",
)


def sentiment_confidence(
    sources: tuple[SentimentSourceInput, ...],
) -> SentimentConfidence:
    """Apply the fixed high/medium/low source-quality contract."""

    applicable = tuple(source for source in sources if source.applicable)
    substantive = tuple(
        source
        for source in applicable
        if source.status is SentimentSourceStatus.SUBSTANTIVE
    )
    reliable = tuple(source for source in substantive if not source.degraded)
    has_degradation = any(
        source.status is not SentimentSourceStatus.SUBSTANTIVE
        or source.degraded
        for source in applicable
    )
    if len(reliable) >= 2 and not has_degradation:
        return SentimentConfidence("high", 0.8)
    if reliable or len(substantive) >= 2:
        return SentimentConfidence("medium", 0.55)
    return SentimentConfidence("low", 0.25)


def _source_input(
    *,
    source_id: str,
    label: str,
    body: str,
    records: tuple[ProvenanceRecord, ...],
    temporal_scope: Literal["point_in_time", "live_only"],
    applicable: bool,
    dataset_id: str | None = None,
    structured_numeric_facts: tuple[StructuredNumericFact, ...] = (),
) -> tuple[SentimentSourceInput, PrefetchedEvidenceBlock]:
    block = prefetched_evidence_block(
        body,
        records,
        temporal_scope=temporal_scope,
        dataset_id=dataset_id,
        structured_numeric_facts=structured_numeric_facts,
    )
    timing = " ".join(record.timing.casefold() for record in records)
    lowered = body.strip().casefold()
    if block["content"] is not None:
        status = SentimentSourceStatus.SUBSTANTIVE
    elif (
        lowered.startswith("<no ")
        or "available; no " in timing
        or "no qualifying records" in timing
    ):
        status = SentimentSourceStatus.NO_SIGNAL
    else:
        status = SentimentSourceStatus.UNAVAILABLE
    degraded = (
        temporal_scope == "live_only"
        or status is not SentimentSourceStatus.SUBSTANTIVE
        or any(token in timing for token in _DEGRADED_TIMING_TOKENS)
        or any(token in timing for token in _UNAVAILABLE_TIMING_TOKENS)
    )
    return (
        SentimentSourceInput(
            source_id=source_id,
            label=label,
            status=status,
            applicable=applicable,
            degraded=degraded,
        ),
        block,
    )


def prepare_sentiment_sources(
    *,
    ticker: str,
    end_date: str,
    news_start_date: str,
    social_start_date: str,
    live_run: bool,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
    stocktwits_retrieved_at: str | None,
    reddit_retrieved_at: str | None,
    market_signals: tuple[FetchedSentimentSignal, ...],
) -> tuple[
    tuple[SentimentSourceInput, ...],
    list[PrefetchedEvidenceBlock],
]:
    """Build the source whitelist and matching audit blocks once."""

    news_records = tuple(extract_provenance(news_block))
    if not news_records:
        news_records = (
            ProvenanceRecord(
                evidence="routed ticker news",
                source="unknown",
                requested=f"{news_start_date} to {end_date}",
                effective="unknown",
                timing=(
                    "unavailable"
                    if "unavailable" in news_block.lower()
                    else "no auditable source metadata captured"
                ),
            ),
        )

    market_suffix = market_suffix_of(ticker)

    def social_status(
        body: str,
        retrieved_at: str | None,
    ) -> tuple[str, str, str | None]:
        if market_suffix:
            return "—", "unavailable: no coverage for this market", None
        if not live_run:
            return (
                "—",
                "live-only; unavailable for historical or future date; "
                "vendor not queried",
                None,
            )
        lowered = body.casefold()
        if "unavailable" in lowered:
            return "—", "retrieval unavailable", retrieved_at
        if lowered.startswith("<no "):
            return (
                f"{social_start_date} to {end_date}",
                "available; no messages in current public-feed window",
                retrieved_at,
            )
        return (
            f"{social_start_date} to {end_date}",
            "live source; market-calendar window filtered",
            retrieved_at,
        )

    stocktwits_effective, stocktwits_timing, stocktwits_retrieved = (
        social_status(stocktwits_block, stocktwits_retrieved_at)
    )
    reddit_effective, reddit_timing, reddit_retrieved = social_status(
        reddit_block,
        reddit_retrieved_at,
    )
    stocktwits_record = ProvenanceRecord(
        evidence="retail social messages",
        source="StockTwits",
        requested=f"{social_start_date} to {end_date}",
        effective=stocktwits_effective,
        timing=stocktwits_timing,
        retrieved_at=stocktwits_retrieved,
    )
    reddit_record = ProvenanceRecord(
        evidence="community discussion",
        source="Reddit public feeds",
        requested=f"{social_start_date} to {end_date}",
        effective=reddit_effective,
        timing=reddit_timing,
        retrieved_at=reddit_retrieved,
    )

    sources: list[SentimentSourceInput] = []
    evidence_blocks: list[PrefetchedEvidenceBlock] = []

    def add_source(
        *,
        source_id: str,
        label: str,
        body: str,
        records: tuple[ProvenanceRecord, ...],
        temporal_scope: Literal["point_in_time", "live_only"],
        applicable: bool,
        dataset_id: str | None = None,
        structured_numeric_facts: tuple[StructuredNumericFact, ...] = (),
    ) -> None:
        source, block = _source_input(
            source_id=source_id,
            label=label,
            body=body,
            records=records,
            temporal_scope=temporal_scope,
            applicable=applicable,
            dataset_id=dataset_id,
            structured_numeric_facts=structured_numeric_facts,
        )
        sources.append(source)
        evidence_blocks.append(block)

    add_source(
        source_id="news",
        label="Routed ticker news",
        body=news_block,
        records=news_records,
        temporal_scope="point_in_time",
        applicable=True,
    )
    social_applicable = not market_suffix and live_run
    add_source(
        source_id="stocktwits",
        label="StockTwits",
        body=stocktwits_block,
        records=(stocktwits_record,),
        temporal_scope="live_only",
        applicable=social_applicable,
    )
    add_source(
        source_id="reddit",
        label="Reddit public feeds",
        body=reddit_block,
        records=(reddit_record,),
        temporal_scope="live_only",
        applicable=social_applicable,
    )

    for result in market_signals:
        spec = result.spec
        body = result.body
        body_records = tuple(extract_provenance(body))
        if not body_records:
            lowered = body.casefold()
            if "unavailable" in lowered:
                record_timing = "unavailable"
                record_effective = "—"
            elif "skipped" in lowered or "no edinet code" in lowered:
                record_timing = "not queried; identifier unavailable"
                record_effective = "—"
            elif body:
                record_timing = spec.timing
                record_effective = spec.effective(end_date)
            elif spec.live_only:
                record_timing = (
                    "live-only; unavailable for historical or future "
                    "date; vendor not queried"
                    if not live_run
                    else "no analyst snapshot returned; retrieval success unknown"
                )
                record_effective = "—"
            else:
                record_timing = "available; no qualifying records"
                record_effective = spec.effective(end_date)
            body_records = (
                ProvenanceRecord(
                    evidence=spec.evidence,
                    source=spec.source,
                    requested=end_date,
                    effective=record_effective,
                    timing=record_timing,
                    retrieved_at=result.retrieved_at,
                ),
            )
        add_source(
            source_id=f"signal.{spec.tag}",
            label=spec.title,
            body=body,
            records=body_records,
            temporal_scope=(
                "live_only" if spec.live_only else "point_in_time"
            ),
            applicable=not (spec.live_only and not live_run),
            dataset_id=spec.dataset_id,
            structured_numeric_facts=result.structured_numeric_facts,
        )

    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("sentiment source catalog contains duplicate source_id values")
    return tuple(sources), evidence_blocks
