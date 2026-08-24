"""Deterministic, market-local Incremental collection planning and gating."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.dataflows.symbol_utils import match_exchange_suffix, normalize_symbol

from .contracts import (
    BenchmarkContext,
    BenchmarkSeriesResult,
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
    CollectionSummary,
    CollectionTemporalBasis,
    EvidenceItem,
    IncrementalCollectionRequest,
    IncrementalCollectionResult,
    IncrementalEvidenceBinding,
    IncrementalEvidenceCandidate,
    InformationAdvancement,
    MarketSeriesResult,
    PerformanceCalculationRecord,
    PerformanceComponent,
    PerformanceComponentStatus,
    PerformanceObservation,
    ResearchAvailability,
    ResearchAvailabilityDomain,
    ResearchAvailabilityStatus,
)

IncrementalCollector = Callable[[IncrementalCollectionRequest], IncrementalCollectionResult]

_MARKET_IDENTITIES = {
    ".T": ("japan", ".T"),
    ".SS": ("mainland_china", ".SS"),
    ".SZ": ("mainland_china", ".SZ"),
}
_MARKET_TIMEZONES = {
    "united_states": ZoneInfo("America/New_York"),
    "japan": ZoneInfo("Asia/Tokyo"),
    "mainland_china": ZoneInfo("Asia/Shanghai"),
}


def derive_research_availability(summary: CollectionSummary) -> ResearchAvailability:
    """Describe actual domain breadth without inferring source completeness."""
    domains = tuple(
        ResearchAvailabilityDomain(
            domain=result.domain,
            status=(
                ResearchAvailabilityStatus.AVAILABLE
                if result.state is CollectionResultState.DATA
                and result.temporal_bases == (CollectionTemporalBasis.PIT,)
                and result.diagnostic is None
                and not any(source.diagnostic is not None for source in result.sources)
                and not result.omitted_by_temporal_boundary
                else (
                    ResearchAvailabilityStatus.LIMITED
                    if result.state in {CollectionResultState.DATA, CollectionResultState.PARTIAL}
                    else ResearchAvailabilityStatus.MISSING
                )
            ),
        )
        for result in summary.domains
    )
    return ResearchAvailability(version=summary.version, domains=domains)


def admit_incremental_observations(
    request: IncrementalCollectionRequest,
    candidates: tuple[IncrementalEvidenceCandidate, ...],
    *,
    sealed_at: datetime,
) -> tuple[EvidenceItem, ...]:
    """Admit strict PIT or bounded Near-live observations without relabeling them."""
    if sealed_at.tzinfo is None or sealed_at.utcoffset() is None:
        raise ValueError("sealed_at must include a timezone")
    normalized: dict[str, EvidenceItem] = {}
    for candidate in candidates:
        item = candidate.evidence
        if item.available_at is not None or candidate.available_on is not None:
            if item.origins and any(
                origin.temporal_scope.value != "point_in_time" for origin in item.origins
            ):
                raise ValueError("live-only observations cannot claim PIT availability")
            resolved = _resolve_incremental_evidence(request, candidate)
            if not request.window_start < resolved.available_at <= request.window_end:
                raise ValueError(
                    "Incremental Evidence availability must lie in the baseline-to-cutoff window"
                )
        else:
            retrieval_times = _near_live_retrieval_times(item)
            if any(retrieved_at > sealed_at for retrieved_at in retrieval_times):
                raise ValueError("Near-live retrieval time cannot be after sealing")
            zone = _MARKET_TIMEZONES[request.market]
            ages = tuple(
                (retrieved_at.astimezone(zone).date() - request.analysis_cutoff).days
                for retrieved_at in retrieval_times
            )
            if any(age < 0 or age > request.near_live_max_age_days for age in ages):
                continue
            resolved = item
        previous = normalized.get(resolved.ref)
        if previous is not None and previous != resolved:
            raise ValueError("Incremental Evidence reference collides with a different payload")
        normalized[resolved.ref] = resolved
    return tuple(normalized.values())


def _near_live_retrieval_times(item: EvidenceItem) -> tuple[datetime, ...]:
    if not item.origins or any(
        origin.temporal_scope.value != "live_only" or origin.retrieved_at is None
        for origin in item.origins
    ):
        raise ValueError(
            "Incremental Evidence requires reliable PIT availability or live-only retrieval time"
        )
    retrieval_times = []
    for origin in item.origins:
        try:
            retrieved_at = datetime.fromisoformat(origin.retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Near-live retrieval time must be an ISO datetime") from exc
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("Near-live retrieval time must include a timezone")
        retrieval_times.append(retrieved_at)
    return tuple(retrieval_times)


def normalize_incremental_collection(
    request: IncrementalCollectionRequest,
    collected: IncrementalCollectionResult,
    *,
    sealed_at: datetime,
) -> tuple[
    CollectionSummary,
    tuple[EvidenceItem, ...],
    tuple[IncrementalEvidenceBinding, ...],
]:
    """Resolve final Evidence identities and align the actual-result summary."""
    summary = collected.collection_summary
    if summary.version != request.version or summary.market != request.market:
        raise ValueError("Collection Summary does not match its frozen request")
    if tuple(result.domain for result in summary.domains) != request.enabled_domains:
        raise ValueError("Collection Summary must contain each enabled domain exactly once")
    if any(
        source.retrieved_at > sealed_at for result in summary.domains for source in result.sources
    ):
        raise ValueError("domain retrieval cannot be after sealing")
    if any(
        boundary is not None and boundary > sealed_at
        for result in summary.domains
        for boundary in (result.observed_from, result.observed_through)
    ):
        raise ValueError("observed collection window cannot be after sealing")

    ref_map: dict[str, EvidenceItem | None] = {}
    admitted_by_ref: dict[str, EvidenceItem] = {}
    for candidate in collected.evidence:
        admitted = admit_incremental_observations(
            request,
            (candidate,),
            sealed_at=sealed_at,
        )
        final_item = admitted[0] if admitted else None
        previous = ref_map.setdefault(candidate.evidence.ref, final_item)
        if previous != final_item:
            raise ValueError(
                "Incremental Evidence caller reference collides with different final payloads"
            )
        if final_item is not None:
            admitted_by_ref[final_item.ref] = final_item

    assigned_refs: list[str] = []
    normalized_domains = []
    for result in summary.domains:
        final_items = []
        omitted_refs = []
        for ref in result.evidence_refs:
            if ref not in ref_map:
                raise ValueError("Collection Summary references uncollected Evidence")
            item = ref_map[ref]
            if item is not None:
                final_items.append(item)
            else:
                omitted_refs.append(ref)
        final_refs = tuple(dict.fromkeys(item.ref for item in final_items))
        assigned_refs.extend(final_refs)
        if final_items:
            actual_source_provenance: dict[str, tuple[bool, datetime | None]] = {}
            for item in final_items:
                for source, fallback, retrieved_at in _item_source_provenance(item):
                    previous_fallback, previous_retrieval = actual_source_provenance.get(
                        source,
                        (False, None),
                    )
                    known_retrievals = tuple(
                        value for value in (previous_retrieval, retrieved_at) if value is not None
                    )
                    actual_source_provenance[source] = (
                        previous_fallback or fallback,
                        max(known_retrievals) if known_retrievals else None,
                    )
            reported_source_provenance = {
                source.source: (
                    source.fallback,
                    source.retrieved_at,
                    source.diagnostic,
                )
                for source in result.sources
            }
            unrepresented_sources = set(reported_source_provenance) - set(actual_source_provenance)
            if (
                not set(actual_source_provenance).issubset(reported_source_provenance)
                or any(
                    actual_fallback != reported_source_provenance[source][0]
                    or (
                        actual_retrieval is not None
                        and actual_retrieval != reported_source_provenance[source][1]
                    )
                    for source, (
                        actual_fallback,
                        actual_retrieval,
                    ) in actual_source_provenance.items()
                )
                or any(
                    reported_source_provenance[source][2] is None
                    for source in unrepresented_sources
                )
            ):
                raise ValueError("collection source provenance must match admitted Evidence")
        temporal_bases = tuple(
            dict.fromkeys(
                CollectionTemporalBasis.PIT
                if item.available_at is not None
                else CollectionTemporalBasis.NEAR_LIVE_ADVISORY
                for item in final_items
            )
        )
        state = result.state
        diagnostic = result.diagnostic
        if state in {CollectionResultState.DATA, CollectionResultState.PARTIAL} and not final_refs:
            state = CollectionResultState.EMPTY
            diagnostic = diagnostic or CollectionDiagnostic(code="outside_temporal_boundary")
        elif final_refs and omitted_refs:
            state = CollectionResultState.PARTIAL
            diagnostic = diagnostic or CollectionDiagnostic(code="outside_temporal_boundary")
        normalized_domains.append(
            CollectionDomainResult(
                domain=result.domain,
                state=state,
                sources=result.sources,
                observed_from=result.observed_from,
                observed_through=result.observed_through,
                temporal_bases=temporal_bases,
                evidence_refs=final_refs,
                diagnostic=diagnostic,
                omitted_by_temporal_boundary=(
                    result.omitted_by_temporal_boundary or bool(omitted_refs)
                ),
            )
        )
    if len(assigned_refs) != len(set(assigned_refs)):
        raise ValueError("Incremental Evidence must belong to exactly one domain result")
    if set(assigned_refs) != set(admitted_by_ref):
        raise ValueError("Collection Summary must reference every admitted Evidence item")
    return (
        CollectionSummary(
            version=summary.version,
            market=summary.market,
            domains=tuple(normalized_domains),
        ),
        tuple(admitted_by_ref.values()),
        tuple(
            IncrementalEvidenceBinding(
                candidate_ref=candidate_ref,
                admitted_ref=item.ref if item is not None else None,
            )
            for candidate_ref, item in ref_map.items()
        ),
    )


def _item_source_provenance(
    item: EvidenceItem,
) -> tuple[tuple[str, bool, datetime | None], ...]:
    if not item.origins:
        return ((item.source, item.fallback, None),)
    provenance = []
    for origin in item.origins:
        retrieved_at = None
        if origin.retrieved_at is not None:
            try:
                retrieved_at = datetime.fromisoformat(origin.retrieved_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Evidence origin retrieval time must be an ISO datetime") from exc
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                raise ValueError("Evidence origin retrieval time must include a timezone")
        provenance.append((origin.source, origin.fallback, retrieved_at))
    return tuple(provenance)


def calculate_stock_performance(
    request: IncrementalCollectionRequest,
    series: MarketSeriesResult | None,
) -> PerformanceObservation:
    """Seal stock endpoints selected from one broader completed-session series."""
    if series is None:
        return PerformanceObservation(
            stock=PerformanceComponent(
                status=PerformanceComponentStatus.UNAVAILABLE,
                reason="No usable stock market series was collected.",
            )
        )
    if series.instrument != request.instrument:
        raise ValueError("stock market series instrument does not match its frozen request")
    return PerformanceObservation(stock=_calculate_performance_component(request, series))


def calculate_benchmark_performance(
    request: IncrementalCollectionRequest,
    benchmarks: tuple[BenchmarkSeriesResult, ...],
) -> tuple[BenchmarkContext, ...]:
    """Calculate benchmark endpoints from the actual collected series."""
    return tuple(
        BenchmarkContext(
            name=benchmark.name,
            component=(
                _calculate_performance_component(request, benchmark.series)
                if benchmark.series is not None
                else PerformanceComponent(
                    status=PerformanceComponentStatus.UNAVAILABLE,
                    reason=(f"Benchmark unavailable: {benchmark.unavailable_diagnostic.code}."),
                )
            ),
        )
        for benchmark in benchmarks
    )


def _calculate_performance_component(
    request: IncrementalCollectionRequest,
    series: MarketSeriesResult,
) -> PerformanceComponent:
    start_points = tuple(
        point for point in series.points if point.completed_at <= request.window_start
    )
    end_points = tuple(point for point in series.points if point.completed_at <= request.window_end)
    if not start_points or not end_points:
        return PerformanceComponent(
            status=PerformanceComponentStatus.UNAVAILABLE,
            reason="The series does not contain both eligible endpoint sessions.",
        )
    start = start_points[-1]
    end = end_points[-1]
    if start.session == end.session:
        return PerformanceComponent(
            status=PerformanceComponentStatus.NOT_YET_OBSERVABLE,
            reason="Both cutoffs resolve to the same completed session.",
        )
    return PerformanceComponent(
        status=PerformanceComponentStatus.CALCULATED,
        calculation=PerformanceCalculationRecord(
            provider=series.source,
            fallback=series.fallback,
            adjustment_basis=series.adjustment_basis,
            retrieved_at=series.retrieved_at,
            baseline_information_cutoff_at=request.window_start,
            target_information_cutoff_at=request.window_end,
            start_session=start.session,
            end_session=end.session,
            start_value=start.adjusted_close,
            end_value=end.adjusted_close,
            unrounded_return=(end.adjusted_close / start.adjusted_close) - 1,
        ),
    )


def assess_information_advancement(
    *,
    baseline_items: tuple[EvidenceItem, ...],
    current_items: tuple[EvidenceItem, ...],
    performance: PerformanceObservation,
    stock_series_admitted: bool,
) -> InformationAdvancement:
    """Detect new observations without treating retrieval-only refresh as information."""
    baseline_ids = {_incremental_observation_identity(item) for item in baseline_items}
    observation_ids = tuple(
        dict.fromkeys(
            identity
            for item in current_items
            if (identity := _incremental_observation_identity(item)) not in baseline_ids
        )
    )
    reasons = []
    if observation_ids:
        reasons.append("admissible_observation")
    if performance.stock.status is PerformanceComponentStatus.CALCULATED and stock_series_admitted:
        reasons.append("completed_stock_session")
    return InformationAdvancement(
        advanced=bool(reasons),
        reasons=tuple(reasons),
        observation_ids=observation_ids,
    )


def _incremental_observation_identity(item: EvidenceItem) -> str:
    origins = [
        {
            "effective": origin.effective,
            "effective_date": (
                origin.effective_date.isoformat() if origin.effective_date else None
            ),
            "timing": origin.timing,
        }
        for origin in item.origins
    ]
    payload = {
        "evidence_type": item.evidence_type,
        "effective_date": item.effective_date.isoformat() if item.effective_date else None,
        "available_at": item.available_at.isoformat() if item.available_at else None,
        "content": item.content,
        "value": item.value,
        "measurement_kind": item.measurement_kind.value,
        "unit": item.unit,
        "origins": origins,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"ob_{digest[:16]}"


def incremental_market_identity(ticker: str) -> dict[str, str]:
    """Parse the supported market and routing suffix once for a frozen Run."""
    canonical_ticker = normalize_symbol(ticker)
    suffix = match_exchange_suffix(canonical_ticker, _MARKET_IDENTITIES)
    market, route_suffix = _MARKET_IDENTITIES.get(
        suffix,
        ("united_states", ""),
    )
    return {"market": market, "route_suffix": route_suffix}


def build_incremental_collection_request(
    *,
    instrument: str,
    baseline_analysis_cutoff,
    analysis_cutoff,
    market_identity: Mapping[str, Any],
    data_routes: Mapping[str, Any],
    data_availability_policy: Mapping[str, Any],
    enabled_domains: tuple[str, ...],
    window_start: datetime,
    window_end: datetime,
) -> IncrementalCollectionRequest:
    """Freeze one common request without expanding configured provider attempts."""
    return IncrementalCollectionRequest(
        version=str(data_availability_policy["version"]),
        instrument=normalize_symbol(instrument),
        market=market_identity["market"],
        route_suffix=market_identity["route_suffix"],
        baseline_analysis_cutoff=baseline_analysis_cutoff,
        analysis_cutoff=analysis_cutoff,
        window_start=window_start,
        window_end=window_end,
        enabled_domains=enabled_domains,
        configured_routes=dict(data_routes),
        near_live_max_age_days=int(data_availability_policy["near_live_max_age_days"]),
    )


def default_incremental_collector(
    request: IncrementalCollectionRequest,
) -> IncrementalCollectionResult:
    """Return truthful unavailable domains until a market ticket connects routing."""
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(
                CollectionDomainResult(
                    domain=domain,
                    state=CollectionResultState.UNAVAILABLE,
                    diagnostic=CollectionDiagnostic(code="market_path_not_connected"),
                )
                for domain in request.enabled_domains
            ),
        )
    )


def _resolve_incremental_evidence(
    plan: IncrementalCollectionRequest,
    candidate: IncrementalEvidenceCandidate,
) -> EvidenceItem:
    """Build final Evidence identity after resolving conservative availability."""
    zone = _MARKET_TIMEZONES[plan.market]
    available_at = candidate.evidence.available_at
    if available_at is None:
        if candidate.available_on is None:
            raise ValueError("Incremental Evidence requires reliable availability")
        available_at = datetime.combine(candidate.available_on, time.max, tzinfo=zone)
    elif available_at.tzinfo is None or available_at.utcoffset() is None:
        raise ValueError("Incremental Evidence availability must include a timezone")
    item = candidate.evidence
    return EvidenceItem.create(
        source=item.source,
        evidence_type=item.evidence_type,
        requested_date=item.requested_date,
        effective_date=item.effective_date,
        available_at=available_at.astimezone(UTC),
        content=item.content,
        value=item.value,
        measurement_kind=item.measurement_kind,
        unit=item.unit,
        quality=item.quality,
        fallback=item.fallback,
        origins=item.origins,
        provenance=item.provenance,
    )
