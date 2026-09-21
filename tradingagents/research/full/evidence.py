"""Seal Full-research tool and producer material into auditable Evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from typing import Any

from langchain_core.messages import ToolMessage

from tradingagents.data.evidence_workset import artifact_records, is_evidence_tool_artifact
from tradingagents.data.lookahead import is_near_live
from tradingagents.domain.data import ProvenanceRecord, SourceObservation
from tradingagents.domain.data_quality import temporal_scope_from_records
from tradingagents.domain.evidence import (
    EvidenceItem,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceTemporalScope,
)
from tradingagents.provenance import (
    extract_evidence_spans,
    extract_provenance,
    strip_provenance_markers,
)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def observation_evidence(
    observation: SourceObservation,
    requested_date: date,
    instrument: str | None,
) -> EvidenceItem:
    """Apply the existing near-live boundary at every Full observation entrance."""
    if observation.is_pit or is_near_live(
        requested_date.isoformat(), instrument, now=observation.retrieved_at,
    ):
        return observation.evidence(requested_date, instrument=instrument)
    timing = observation.timing + "; unavailable outside market-local near-live window"
    if observation.fallback:
        timing += "; fallback source used"
    return evidence_from_records(
        (ProvenanceRecord(
            evidence=observation.kind,
            source=observation.source,
            requested=requested_date.isoformat(),
            effective=str(observation.effective_date or "retrieval-time snapshot"),
            timing=timing,
            retrieved_at=observation.retrieved_at.isoformat(),
        ),),
        requested_date=requested_date,
        content=None,
        temporal_scope=EvidenceTemporalScope.LIVE_ONLY,
    )


def collect_evidence(
    messages: Iterable[Any],
    *,
    requested_date: date,
    analyst: str,
    prefetched_blocks: Iterable[dict[str, Any]] = (),
    instrument: str | None = None,
) -> list[EvidenceItem]:
    items: dict[str, EvidenceItem] = {}
    content_groups: dict[
        tuple[str, EvidenceTemporalScope],
        list[ProvenanceRecord],
    ] = {}
    content_metadata: dict[
        tuple[str, EvidenceTemporalScope],
        dict[str, Any],
    ] = {}
    content_order: list[tuple[str, EvidenceTemporalScope]] = []
    empty_payloads: list[tuple[tuple[ProvenanceRecord, ...], EvidenceTemporalScope]] = []

    def collect_payload(
        records: Iterable[ProvenanceRecord],
        content: str | None,
        temporal_scope: str | EvidenceTemporalScope | None = None,
        provenance_metadata: dict[str, Any] | None = None,
    ) -> None:
        records = tuple(dict.fromkeys(records))
        if not records:
            return
        scope = _coerce_temporal_scope(temporal_scope, records)
        if content:
            key = (content, scope)
            if key not in content_groups:
                content_groups[key] = []
                content_order.append(key)
                content_metadata[key] = dict(provenance_metadata or {})
            elif provenance_metadata:
                content_metadata[key].update(provenance_metadata)
            existing = content_groups[key]
            for record in records:
                if record not in existing:
                    existing.append(record)
        else:
            empty_payloads.append((records, scope))

    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    for message in tool_messages:
        if isinstance(message.content, str) and "<!-- news-observation:" in message.content:
            from tradingagents.data.news_selection import emit_news
            from tradingagents.data.source_observations import capture_observations

            with capture_observations() as news_observations:
                emit_news(message.content, "news", instrument or "", global_news=getattr(message, "name", "") == "get_global_news", metadata_only=True)
            records = extract_provenance(message.content)
            fallback = any("fallback vendor selected" in record.timing for record in records)
            for observation in news_observations:
                observation = replace(observation, fallback=observation.fallback or fallback)
                item = observation_evidence(observation, requested_date, instrument)
                items[item.ref] = item
            # Preserve collection diagnostics without assigning article content or identity.
            collect_payload(records, None)
            # Producer metadata owns item timing, including cached revisions.
            continue
        artifact = getattr(message, "artifact", None)
        if is_evidence_tool_artifact(artifact):
            records = artifact_records(artifact)
            if not records:
                records = (
                    ProvenanceRecord(
                        evidence=str(
                            artifact.get("evidence_type")
                            or getattr(message, "name", None)
                            or f"{analyst} tool"
                        ),
                        source="unknown",
                        requested=requested_date.isoformat(),
                        effective="unknown",
                        timing="no auditable source metadata captured",
                    ),
                )
            collect_payload(
                records,
                str(artifact["source_content"]).strip() or None,
                artifact.get("temporal_scope"),
                {
                    "dataset_id": artifact.get("dataset_id"),
                    "analytical_views": artifact.get("analytical_views", {}),
                    "column_measurements": artifact.get("column_measurements", {}),
                    "structured_numeric_facts": artifact.get(
                        "structured_numeric_facts", []
                    ),
                },
            )
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        spans = extract_evidence_spans(content)
        if spans:
            for span in spans:
                records = list(span.records)
                if not records:
                    records = [
                        ProvenanceRecord(
                            evidence=getattr(message, "name", None) or f"{analyst} tool",
                            source="unknown",
                            requested=requested_date.isoformat(),
                            effective="unknown",
                            timing=(
                                f"{span.temporal_scope} span without auditable source metadata"
                            ),
                        )
                    ]
                collect_payload(
                    records,
                    span.content,
                    span.temporal_scope,
                )
            continue
        records = extract_provenance(content)
        if not records:
            records = [
                ProvenanceRecord(
                    evidence=getattr(message, "name", None) or f"{analyst} tool",
                    source="unknown",
                    requested=requested_date.isoformat(),
                    effective="unknown",
                    timing="no auditable source metadata captured",
                )
            ]
        collect_payload(
            records,
            strip_provenance_markers(content).strip() or None,
        )

    for block in prefetched_blocks:
        if block.get("source_observation"):
            observation = SourceObservation.load(block["source_observation"])
            item = observation_evidence(observation, requested_date, instrument)
            items[item.ref] = item
            continue
        raw_records = block.get("records", [])
        records = []
        for raw in raw_records:
            try:
                records.append(ProvenanceRecord(**raw))
            except (TypeError, ValueError):
                continue
        collect_payload(
            records,
            block.get("content"),
            block.get("temporal_scope"),
            {
                "structured_numeric_facts": block.get(
                    "structured_numeric_facts", []
                )
            },
        )

    for content, scope in content_order:
        item = evidence_from_records(
            content_groups[(content, scope)],
            requested_date=requested_date,
            content=content,
            temporal_scope=scope,
            provenance_metadata=content_metadata[(content, scope)],
        )
        items[item.ref] = item
    for records, scope in empty_payloads:
        item = evidence_from_records(
            records,
            requested_date=requested_date,
            content=None,
            temporal_scope=scope,
        )
        items[item.ref] = item
    if not items:
        item = evidence_from_record(
            ProvenanceRecord(
                evidence=f"{analyst} analyst evidence",
                source="unknown",
                requested=requested_date.isoformat(),
                effective="unknown",
                timing="no auditable source metadata captured",
            ),
            requested_date=requested_date,
            content=None,
        )
        items[item.ref] = item
    return list(items.values())


def evidence_from_record(
    record: ProvenanceRecord,
    *,
    requested_date: date,
    content: str | None,
) -> EvidenceItem:
    return evidence_from_records(
        (record,),
        requested_date=requested_date,
        content=content,
    )


def evidence_from_records(
    records: Iterable[ProvenanceRecord],
    *,
    requested_date: date,
    content: str | None,
    temporal_scope: str | EvidenceTemporalScope | None = None,
    provenance_metadata: dict[str, Any] | None = None,
) -> EvidenceItem:
    records = tuple(records)
    if not records:
        raise ValueError("at least one provenance record is required")
    origin_pairs = tuple(
        _origin_from_record(
            record,
            requested_date=requested_date,
            temporal_scope=temporal_scope,
        )
        for record in records
    )
    origins = tuple(origin for origin, _future in origin_pairs)
    future_dated = any(future for _origin, future in origin_pairs)
    valid_effective_dates = [
        origin.effective_date
        for origin in origins
        if origin.effective_date is not None and origin.effective_date <= requested_date
    ]
    effective = max(valid_effective_dates) if valid_effective_dates else None
    all_unavailable = all(origin.quality is EvidenceQuality.UNAVAILABLE for origin in origins)
    all_reliable = all(
        origin.quality is EvidenceQuality.HIGH and not origin.fallback for origin in origins
    )
    temporal_scopes = tuple(dict.fromkeys(origin.temporal_scope for origin in origins))
    mixed_temporal_scope = len(temporal_scopes) > 1
    quality = (
        EvidenceQuality.UNAVAILABLE
        if all_unavailable
        else EvidenceQuality.HIGH
        if all_reliable
        else EvidenceQuality.LOW
    )
    sources = tuple(dict.fromkeys(origin.source for origin in origins))
    evidence_types = tuple(dict.fromkeys(origin.evidence_type for origin in origins))
    composite = len(origins) > 1
    source = sources[0] if not composite else "composite"
    evidence_type = evidence_types[0] if len(evidence_types) == 1 else "composite tool response"
    fallback = any(origin.fallback for origin in origins)
    provenance = (
        {
            "requested": origins[0].requested,
            "effective": origins[0].effective,
            "timing": origins[0].timing,
            "retrieved_at": origins[0].retrieved_at,
        }
        if not composite
        else {
            "composite": True,
            "origin_count": len(origins),
            "temporal_scopes": [scope.value for scope in temporal_scopes],
            "mixed_temporal_scope_unseparated": mixed_temporal_scope,
        }
    )
    provenance.update(provenance_metadata or {})
    return EvidenceItem.create(
        source=source,
        evidence_type=evidence_type,
        requested_date=requested_date,
        effective_date=effective,
        content=(None if future_dated or all_unavailable or mixed_temporal_scope else content),
        quality=quality,
        fallback=fallback,
        origins=origins,
        provenance=provenance,
    )


def _origin_from_record(
    record: ProvenanceRecord,
    *,
    requested_date: date,
    temporal_scope: str | EvidenceTemporalScope | None = None,
) -> tuple[EvidenceOrigin, bool]:
    effective = _last_date(record.effective)
    future_dated = bool(effective and effective > requested_date)
    timing = record.timing.casefold()
    scope = _coerce_temporal_scope(temporal_scope, (record,))
    unavailable = (
        any(
            token in timing
            for token in (
                "unavailable",
                "failed",
                "not requested",
                "not queried",
                "no usable data",
                "no auditable source metadata",
            )
        )
        or future_dated
    )
    degraded = scope is EvidenceTemporalScope.LIVE_ONLY or any(
        token in timing
        for token in (
            "fallback",
            "partial",
            "stale",
            "truncated",
            "non-point-in-time",
            "non-vintage",
        )
    )
    successful_empty = timing.startswith("available;") and (
        "; no " in timing or "contained no values" in timing
    )
    missing_effective = effective is None and not successful_empty
    quality = (
        EvidenceQuality.UNAVAILABLE
        if unavailable
        else EvidenceQuality.LOW
        if (degraded or missing_effective or record.source.casefold() in {"unknown", "—", ""})
        else EvidenceQuality.HIGH
    )
    display_timing = (
        f"{record.timing}; future-dated evidence withheld" if future_dated else record.timing
    )
    return (
        EvidenceOrigin(
            source=record.source or "unknown",
            evidence_type=record.evidence or "unknown evidence",
            requested=record.requested or "unknown",
            effective=record.effective or "unknown",
            effective_date=effective,
            timing=display_timing or "unknown",
            retrieved_at=record.retrieved_at,
            quality=quality,
            fallback="fallback" in timing,
            temporal_scope=scope,
        ),
        future_dated,
    )


def _coerce_temporal_scope(
    value: str | EvidenceTemporalScope | None,
    records: Iterable[ProvenanceRecord],
) -> EvidenceTemporalScope:
    if isinstance(value, EvidenceTemporalScope):
        return (
            EvidenceTemporalScope(temporal_scope_from_records(records))
            if value is EvidenceTemporalScope.UNKNOWN
            else value
        )
    raw = value or temporal_scope_from_records(records)
    if raw == EvidenceTemporalScope.UNKNOWN.value:
        raw = temporal_scope_from_records(records)
    try:
        return EvidenceTemporalScope(raw)
    except ValueError:
        return EvidenceTemporalScope.UNKNOWN


def _last_date(value: str | None) -> date | None:
    matches = _DATE_RE.findall(value or "")
    if not matches:
        return None
    try:
        return max(date.fromisoformat(raw) for raw in matches)
    except ValueError:
        return None
