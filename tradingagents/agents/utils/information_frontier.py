"""Point-in-time filtering shared by tool-called and prefetched Evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import date, datetime
from typing import Any

from tradingagents.provenance import (
    ProvenanceRecord,
    SourceWatermark,
    attach_source_watermarks,
    extract_evidence_spans,
    extract_provenance,
    extract_source_observations,
    extract_source_watermarks,
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
) -> tuple[str, bool]:
    """Fail closed before source text enters an analyst conversation."""

    if information_frontier is None:
        return content, False
    observations = extract_source_observations(content)
    watermarks = extract_source_watermarks(content)
    spans = extract_evidence_spans(content)
    plain_records = extract_provenance(content) if not spans else ()
    frontier_date = information_frontier.date()
    has_attestation = bool(
        observations or watermarks or spans or plain_records or external_attestation
    )
    should_omit = (
        not has_attestation
        or temporal_scope in {"live_only", "unknown"}
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
            limitations=(FRONTIER_UNSAFE_MESSAGE,),
            limitation_kind="unknown",
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
