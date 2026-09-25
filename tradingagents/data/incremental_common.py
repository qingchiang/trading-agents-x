"""Shared observation normalization; market timing and availability stay local."""

from datetime import date

from tradingagents.domain.collection import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
)
from tradingagents.domain.data import EvidenceSpan
from tradingagents.domain.data_quality import temporal_scope_from_records
from tradingagents.domain.evidence import EvidenceOrigin


class CollectionUnavailable(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def bounded_empty(domain, sources):
    return CollectionDomainResult(
        domain=domain,
        state=CollectionResultState.EMPTY,
        sources=sources,
        diagnostic=CollectionDiagnostic(code="bounded_feed_no_observed_records"),
    )


def unavailable(domain, code, *, sources=()):
    return CollectionDomainResult(
        domain=domain,
        state=CollectionResultState.UNAVAILABLE,
        sources=tuple(
            source.model_copy(update={"diagnostic": CollectionDiagnostic(code=code)})
            for source in sources
        ),
        diagnostic=CollectionDiagnostic(code=code),
    )


def merge_sources(*source_groups):
    merged = {}
    for group in source_groups:
        for source in group:
            merged[source.source] = source
    return tuple(merged.values())


def origin_effective_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def origin_from_record(record, source, evidence_type, *, temporal_scope):
    return EvidenceOrigin(
        source=source.source,
        evidence_type=evidence_type,
        requested=record.requested or "unknown",
        effective=record.effective or "unknown",
        effective_date=origin_effective_date(record.effective),
        timing=record.timing or "unknown",
        retrieved_at=(
            record.retrieved_at or source.retrieved_at.isoformat().replace("+00:00", "Z")
        ),
        fallback=source.fallback,
        temporal_scope=temporal_scope,
    )


def is_empty(body):
    lowered = body.strip().casefold()
    return not lowered or lowered.startswith("no ")


def is_failure(body):
    return (
        body.strip().casefold().startswith(("error fetching", "error retrieving", "error getting"))
    )


def fundamentals_spans(response, body):
    spans = response.spans
    if spans:
        return tuple(spans)
    records = tuple(response.provenance)
    return (
        EvidenceSpan(
            content=body,
            records=records,
            temporal_scope=temporal_scope_from_records(records),
        ),
    )
