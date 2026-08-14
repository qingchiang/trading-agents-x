"""Point-in-time filtering shared by tool-called and prefetched Evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from tradingagents.application.evidence_admission import (
    evaluate_evidence_admission,
)
from tradingagents.provenance import (
    EvidenceSpan,
    ProvenanceRecord,
    SourceWatermark,
    attach_evidence_span,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_evidence_spans,
    extract_provenance,
    extract_source_observations,
    extract_source_watermarks,
    temporal_scope_from_records,
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FRONTIER_UNSAFE_MESSAGE = (
    "Evidence omitted because its availability could not be attested at or "
    "before the frozen information frontier."
)


def information_frontier_from_state(state: Mapping[str, Any]) -> datetime | None:
    """Read the frozen timezone-aware frontier carried into an analyst subgraph."""

    raw = state.get("information_frontier")
    if raw is None:
        return None
    frontier = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
    if frontier.utcoffset() is None:
        raise ValueError("Information Frontier requires a timezone")
    return frontier


def filter_evidence_content_at_information_frontier(
    content: str,
    information_frontier: datetime | None,
    *,
    fallback_source: str,
    temporal_scope: str | None = None,
    external_attestation: bool = False,
    analysis_date: date | None = None,
    instrument: str | None = None,
    retrieved_at: str | datetime | None = None,
    sealed_at: datetime | None = None,
) -> tuple[str, bool]:
    """Fail closed before source text enters an analyst conversation."""

    if information_frontier is None:
        return content, False
    observations = extract_source_observations(content)
    watermarks = extract_source_watermarks(content)
    spans = extract_evidence_spans(content)
    if spans:
        return _filter_evidence_spans(
            spans,
            information_frontier,
            fallback_source=fallback_source,
            analysis_date=analysis_date,
            instrument=instrument,
            sealed_at=sealed_at,
        )
    plain_records = extract_provenance(content) if not spans else ()
    inferred_scope = temporal_scope or (
        temporal_scope_from_records(plain_records) if plain_records else None
    )
    if (
        inferred_scope == "live_only"
        and analysis_date is not None
        and instrument is not None
    ):
        effective_dates = tuple(
            date.fromisoformat(value)
            for record in plain_records
            for value in _DATE_RE.findall(record.effective)
        )
        retrievals = tuple(
            record.retrieved_at for record in plain_records
        ) or (retrieved_at,)
        if all(
            evaluate_evidence_admission(
                temporal_scope="live_only",
                analysis_date=analysis_date,
                instrument=instrument,
                retrieved_at=value,
                sealed_at=sealed_at,
                effective_dates=effective_dates,
            ).admitted
            for value in retrievals
        ):
            return content, False
    frontier_date = information_frontier.date()
    has_attestation = bool(
        observations or watermarks or spans or plain_records or external_attestation
    )
    should_omit = (
        not has_attestation
        or inferred_scope in {"live_only", "unknown"}
        or any(
            datetime.fromisoformat(observation.available_at) > information_frontier
            for observation in observations
        )
        or any(item.temporal_scope != "point_in_time" for item in watermarks)
        or any(item.temporal_scope != "point_in_time" for item in spans)
        or any(
            provenance_requires_frontier_omission(
                item.records,
                information_frontier,
            )
            for item in spans
            if item.temporal_scope == "point_in_time"
        )
        or provenance_requires_frontier_omission(
            plain_records,
            information_frontier,
        )
        or any(
            date.fromisoformat(item.scanned_end) >= frontier_date
            and not any(
                observation.source == item.source
                for observation in observations
            )
            for item in watermarks
        )
    )
    if not should_omit:
        return content, False
    return (
        frontier_omission_content(
            content,
            information_frontier,
            fallback_source=fallback_source,
        ),
        True,
    )


def _filter_evidence_spans(
    spans: Iterable[EvidenceSpan],
    information_frontier: datetime,
    *,
    fallback_source: str,
    analysis_date: date | None,
    instrument: str | None,
    sealed_at: datetime | None,
) -> tuple[str, bool]:
    """Admit independently auditable temporal spans and redact unsafe bodies."""

    parts: list[str] = []
    omitted = False
    for span in spans:
        serialized = _serialize_evidence_span(span)
        if _span_is_admissible(
            span,
            information_frontier,
            analysis_date=analysis_date,
            instrument=instrument,
            sealed_at=sealed_at,
        ):
            parts.append(serialized)
            continue
        omitted = True
        parts.append(
            frontier_omission_content(
                serialized,
                information_frontier,
                fallback_source=fallback_source,
                fallback_temporal_scope=span.temporal_scope,
                fallback_limitation_kind=(
                    "live_only"
                    if span.temporal_scope == "live_only"
                    else "unknown"
                ),
            )
        )
    return "\n".join(part for part in parts if part), omitted


def _span_is_admissible(
    span: EvidenceSpan,
    information_frontier: datetime,
    *,
    analysis_date: date | None,
    instrument: str | None,
    sealed_at: datetime | None,
) -> bool:
    observations = span.source_observations
    watermarks = span.source_watermarks
    if span.temporal_scope == "point_in_time":
        attested_sources = {
            observation.source for observation in observations
        } | {
            watermark.source
            for watermark in watermarks
            if _watermark_attests_frontier(watermark, information_frontier)
        }
        effective_dates = tuple(
            date.fromisoformat(value)
            for record in span.records
            for value in _DATE_RE.findall(record.effective)
        )
        if analysis_date is not None and instrument is not None:
            pit_decisions = (
                evaluate_evidence_admission(
                    temporal_scope="point_in_time",
                    analysis_date=analysis_date,
                    instrument=instrument,
                    effective_dates=effective_dates,
                    information_frontier=information_frontier,
                ),
                *(
                    evaluate_evidence_admission(
                        temporal_scope="point_in_time",
                        analysis_date=analysis_date,
                        instrument=instrument,
                        effective_dates=effective_dates,
                        available_at=datetime.fromisoformat(
                            observation.available_at
                        ),
                        information_frontier=information_frontier,
                    )
                    for observation in observations
                ),
            )
            if any(not decision.admitted for decision in pit_decisions):
                return False
        return not (
            any(
                datetime.fromisoformat(observation.available_at)
                > information_frontier
                for observation in observations
            )
            or any(item.temporal_scope != "point_in_time" for item in watermarks)
            or provenance_requires_frontier_omission(
                (
                    record
                    for record in span.records
                    if record.source not in attested_sources
                ),
                information_frontier,
            )
            or any(
                date.fromisoformat(item.scanned_end)
                >= information_frontier.date()
                and not any(
                    observation.source == item.source
                    for observation in observations
                )
                and not _watermark_attests_frontier(item, information_frontier)
                for item in watermarks
            )
        )
    if (
        span.temporal_scope != "live_only"
        or analysis_date is None
        or instrument is None
        or not span.records
    ):
        return False
    effective_dates = tuple(
        date.fromisoformat(value)
        for record in span.records
        for value in _DATE_RE.findall(record.effective)
    )
    return all(
        evaluate_evidence_admission(
            temporal_scope="live_only",
            analysis_date=analysis_date,
            instrument=instrument,
            retrieved_at=record.retrieved_at,
            sealed_at=sealed_at,
            effective_dates=effective_dates,
        ).admitted
        for record in span.records
    )


def _watermark_attests_frontier(
    watermark: SourceWatermark,
    information_frontier: datetime,
) -> bool:
    """Accept an empty PIT scan only when its producer froze the same horizon."""

    if (
        watermark.temporal_scope != "point_in_time"
        or watermark.status != "complete"
        or watermark.information_frontier is None
    ):
        return False
    producer_frontier = datetime.fromisoformat(watermark.information_frontier)
    return (
        producer_frontier <= information_frontier
        and date.fromisoformat(watermark.scanned_end) <= producer_frontier.date()
    )


def _serialize_evidence_span(span: EvidenceSpan) -> str:
    content = span.content or ""
    content = attach_provenance(content, *span.records)
    content = attach_source_observations(content, *span.source_observations)
    content = attach_source_watermarks(content, *span.source_watermarks)
    if span.explicit:
        return attach_evidence_span(content, temporal_scope=span.temporal_scope)
    return content


def provenance_requires_frontier_omission(
    records: Iterable[ProvenanceRecord],
    information_frontier: datetime,
) -> bool:
    """Fail closed for current-day provenance without precise availability."""

    records = tuple(records)
    effective_dates = [
        date.fromisoformat(value)
        for record in records
        for value in _DATE_RE.findall(record.effective)
    ]
    if not effective_dates or max(effective_dates) < information_frontier.date():
        return False
    retrieved = []
    for record in records:
        if record.retrieved_at is None:
            return True
        value = datetime.fromisoformat(record.retrieved_at)
        if value.utcoffset() is None:
            return True
        retrieved.append(value)
    return not retrieved or any(value > information_frontier for value in retrieved)


def frontier_omission_content(
    content: str,
    frontier: datetime,
    *,
    fallback_source: str,
    fallback_temporal_scope: str = "point_in_time",
    fallback_limitation_kind: str = "unknown",
) -> str:
    """Record a structured limitation for source content omitted at the frontier."""

    watermarks = extract_source_watermarks(content)
    known_sources = {item.source for item in watermarks}
    sources = {
        record.source
        for record in extract_provenance(content)
        if record.source and record.source not in known_sources
    }
    sources.update(
        observation.source
        for observation in extract_source_observations(content)
        if observation.source not in known_sources
    )
    if not watermarks and not sources:
        sources.add(fallback_source)
    day = frontier.date().isoformat()
    limitations = tuple(
        SourceWatermark(
            source=source,
            scanned_start=day,
            scanned_end=day,
            status="unavailable",
            temporal_scope=fallback_temporal_scope,
            limitations=(FRONTIER_UNSAFE_MESSAGE,),
            limitation_kind=fallback_limitation_kind,
        )
        for source in sorted(sources)
    )
    return attach_source_watermarks(
        FRONTIER_UNSAFE_MESSAGE,
        *(
            SourceWatermark(
                source=item.source,
                scanned_start=item.scanned_start,
                scanned_end=item.scanned_end,
                status="unavailable",
                temporal_scope=item.temporal_scope,
                limitations=(*item.limitations, FRONTIER_UNSAFE_MESSAGE),
                returned_records=0,
                reported_records=item.reported_records,
                requested_interval=item.requested_interval,
                limitation_kind="unknown",
            )
            for item in watermarks
        ),
        *limitations,
    )


def source_metadata(content: str) -> dict[str, list[dict[str, Any]]]:
    """Serialize source-native metadata carried by one prefetched response."""

    metadata = {
        "source_records": [asdict(item) for item in extract_source_observations(content)],
        "source_watermarks": [asdict(item) for item in extract_source_watermarks(content)],
    }
    return {key: value for key, value in metadata.items() if value}
