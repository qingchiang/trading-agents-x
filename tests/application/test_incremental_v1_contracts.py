from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionResultState,
    CollectionSourceProvenance,
    CollectionSummary,
    CollectionTemporalBasis,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceTemporalScope,
    IncrementalCollectionRequest,
    IncrementalCollectionResult,
    IncrementalEvidenceCandidate,
    InformationAdvancement,
    MarketSeriesPoint,
    MarketSeriesResult,
    PerformanceCalculationRecord,
    PerformanceComponentStatus,
    ResearchAvailability,
    ResearchAvailabilityDomain,
    ResearchAvailabilityStatus,
)
from tradingagents.application.incremental_collection import (
    admit_incremental_observations,
    assess_information_advancement,
    calculate_stock_performance,
    derive_research_availability,
    normalize_incremental_collection,
)


def test_collection_summary_records_one_actual_result_per_domain() -> None:
    news = CollectionDomainResult(
        domain="news",
        state=CollectionResultState.EMPTY,
        sources=_sources("yahoo", datetime(2026, 7, 24, 12, tzinfo=UTC)),
        observed_from=datetime(2026, 7, 24, 10, tzinfo=UTC),
        observed_through=datetime(2026, 7, 24, 12, tzinfo=UTC),
    )

    summary = CollectionSummary(
        version="1",
        market="united_states",
        domains=(news,),
    )

    assert summary.model_dump(mode="json") == {
        "version": "1",
        "market": "united_states",
        "domains": [
            {
                "domain": "news",
                "state": "empty",
                "sources": [
                    {
                        "source": "yahoo",
                        "fallback": False,
                        "retrieved_at": "2026-07-24T12:00:00Z",
                    }
                ],
                "observed_from": "2026-07-24T10:00:00Z",
                "observed_through": "2026-07-24T12:00:00Z",
                "temporal_bases": [],
                "evidence_refs": [],
                "diagnostic": None,
            }
        ],
    }
    assert not hasattr(news, "chain_position")
    assert not hasattr(news, "source_watermark")
    assert not hasattr(news, "scanned_from")

    with pytest.raises(ValidationError, match="domains must be unique"):
        CollectionSummary(
            version="1",
            market="united_states",
            domains=(news, news),
        )


def test_collection_summary_preserves_multiple_actual_assembler_sources() -> None:
    summary = CollectionSummary(
        version="1",
        market="japan",
        domains=(
            CollectionDomainResult(
                domain="news",
                state="data",
                sources=(
                    CollectionSourceProvenance(
                        source="edinet",
                        retrieved_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
                    ),
                    CollectionSourceProvenance(
                        source="tdnet",
                        fallback=True,
                        retrieved_at=datetime(2026, 7, 24, 12, 1, tzinfo=UTC),
                    ),
                ),
                temporal_bases=("pit",),
                evidence_refs=("ev_111111111111", "ev_222222222222"),
            ),
        ),
    )

    assert tuple(source.source for source in summary.domains[0].sources) == (
        "edinet",
        "tdnet",
    )


def test_incremental_collection_request_deeply_freezes_configured_routes() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24")

    with pytest.raises(TypeError):
        request.configured_routes["data_vendors"]["fundamentals"] = "other"


def test_collection_domain_result_requires_truthful_state_provenance() -> None:
    with pytest.raises(ValidationError, match="data and partial results require Evidence"):
        CollectionDomainResult(
            domain="news",
            state="data",
            sources=_sources("yahoo", datetime(2026, 7, 24, 12, tzinfo=UTC)),
            temporal_bases=(CollectionTemporalBasis.PIT,),
        )

    with pytest.raises(ValidationError, match="empty and unavailable results cannot"):
        CollectionDomainResult(
            domain="news",
            state="empty",
            sources=_sources("yahoo", datetime(2026, 7, 24, 12, tzinfo=UTC)),
            evidence_refs=("ev_111111111111",),
        )

    with pytest.raises(ValidationError, match="unavailable results require"):
        CollectionDomainResult(domain="social", state="unavailable")

    with pytest.raises(ValidationError, match="partial results require"):
        CollectionDomainResult(
            domain="social",
            state="partial",
            sources=_sources("stocktwits", datetime(2026, 7, 24, 12, tzinfo=UTC)),
            temporal_bases=("near_live_advisory",),
            evidence_refs=("ev_111111111111",),
        )

    unavailable = CollectionDomainResult(
        domain="social",
        state="unavailable",
        diagnostic=CollectionDiagnostic(code="not_configured"),
    )
    assert unavailable.sources == ()


def test_collection_domain_result_rejects_ambiguous_observed_windows() -> None:
    with pytest.raises(ValidationError, match="retrieved_at must include a timezone"):
        CollectionDomainResult(
            domain="news",
            state="empty",
            sources=_sources("yahoo", datetime(2026, 7, 24, 12)),
        )

    with pytest.raises(ValidationError, match="observed window must be complete"):
        CollectionDomainResult(
            domain="news",
            state="empty",
            sources=_sources("yahoo", datetime(2026, 7, 24, 12, tzinfo=UTC)),
            observed_from=datetime(2026, 7, 24, 10, tzinfo=UTC),
        )

    with pytest.raises(ValidationError, match="observed window must be ordered"):
        CollectionDomainResult(
            domain="news",
            state="empty",
            sources=_sources("yahoo", datetime(2026, 7, 24, 12, tzinfo=UTC)),
            observed_from=datetime(2026, 7, 24, 13, tzinfo=UTC),
            observed_through=datetime(2026, 7, 24, 12, tzinfo=UTC),
        )


def test_research_availability_describes_actual_domain_breadth() -> None:
    retrieved_at = datetime(2026, 7, 24, 12, tzinfo=UTC)
    summary = CollectionSummary(
        version="1",
        market="united_states",
        domains=(
            CollectionDomainResult(
                domain="fundamentals",
                state="data",
                sources=_sources("sec", retrieved_at),
                temporal_bases=("pit",),
                evidence_refs=("ev_111111111111",),
            ),
            CollectionDomainResult(
                domain="news",
                state="empty",
                sources=_sources("yahoo", retrieved_at),
            ),
            CollectionDomainResult(
                domain="social",
                state="partial",
                sources=_sources("stocktwits", retrieved_at),
                temporal_bases=("near_live_advisory",),
                evidence_refs=("ev_222222222222",),
                diagnostic=CollectionDiagnostic(code="bounded_feed"),
            ),
            CollectionDomainResult(
                domain="market",
                state="unavailable",
                sources=_sources("yahoo", retrieved_at),
                diagnostic=CollectionDiagnostic(code="transport_timeout"),
            ),
        ),
    )

    assert derive_research_availability(summary) == ResearchAvailability(
        version="1",
        domains=(
            ResearchAvailabilityDomain(
                domain="fundamentals",
                status=ResearchAvailabilityStatus.AVAILABLE,
            ),
            ResearchAvailabilityDomain(
                domain="news",
                status=ResearchAvailabilityStatus.MISSING,
            ),
            ResearchAvailabilityDomain(
                domain="social",
                status=ResearchAvailabilityStatus.LIMITED,
            ),
            ResearchAvailabilityDomain(
                domain="market",
                status=ResearchAvailabilityStatus.MISSING,
            ),
        ),
    )
    assert "requirement" not in derive_research_availability(summary).model_dump_json()


def test_collection_normalization_preserves_actual_fallback_provenance() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24").model_copy(
        update={"enabled_domains": ("news",)}
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fallback.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="Fallback record.",
            fallback=True,
        )
    )
    collected = IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version="1",
            market="united_states",
            domains=(
                CollectionDomainResult(
                    domain="news",
                    state="data",
                    sources=_sources(
                        "fallback.news",
                        datetime(2026, 7, 24, 18, tzinfo=UTC),
                        fallback=True,
                    ),
                    temporal_bases=("pit",),
                    evidence_refs=(candidate.evidence.ref,),
                ),
            ),
        ),
        evidence=(candidate,),
    )

    summary, evidence, _bindings = normalize_incremental_collection(
        request,
        collected,
        sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
    )

    assert summary.domains[0].sources[0].source == "fallback.news"
    assert summary.domains[0].sources[0].fallback is True
    assert evidence[0].fallback is True


@pytest.mark.parametrize(
    ("reported_source", "reported_fallback"),
    [("wrong.news", True), ("fallback.news", False)],
)
def test_collection_normalization_rejects_inconsistent_source_provenance(
    reported_source,
    reported_fallback,
) -> None:
    request = _collection_request(analysis_cutoff="2026-07-24").model_copy(
        update={"enabled_domains": ("news",)}
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fallback.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A filing from the configured fallback.",
            fallback=True,
        )
    )
    collected = IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=(
                CollectionDomainResult(
                    domain="news",
                    state="data",
                    sources=_sources(
                        reported_source,
                        datetime(2026, 7, 24, 18, tzinfo=UTC),
                        fallback=reported_fallback,
                    ),
                    temporal_bases=("pit",),
                    evidence_refs=(candidate.evidence.ref,),
                ),
            ),
        ),
        evidence=(candidate,),
    )

    with pytest.raises(ValueError, match="source and fallback must match admitted Evidence"):
        normalize_incremental_collection(
            request,
            collected,
            sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
        )


def test_collection_normalization_rejects_domain_retrieval_after_sealing() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24")
    collected = IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(
                CollectionDomainResult(
                    domain=domain,
                    state="empty" if domain == "news" else "unavailable",
                    sources=(
                        _sources(
                            "fixture.news",
                            datetime(2026, 7, 24, 19, 1, tzinfo=UTC),
                        )
                        if domain == "news"
                        else ()
                    ),
                    diagnostic=(
                        None if domain == "news" else CollectionDiagnostic(code="not_configured")
                    ),
                )
                for domain in request.enabled_domains
            ),
        )
    )

    with pytest.raises(ValueError, match="domain retrieval cannot be after sealing"):
        normalize_incremental_collection(
            request,
            collected,
            sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
        )


def test_collection_normalization_rejects_same_reference_with_different_payload() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24").model_copy(
        update={"enabled_domains": ("news",)}
    )
    first = EvidenceItem.create(
        source="fixture.news",
        evidence_type="filing",
        requested_date=date(2026, 7, 24),
        available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
        content="First payload.",
    )
    collided = first.model_copy(update={"content": "Different payload."})
    collected = IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version="1",
            market="united_states",
            domains=(
                CollectionDomainResult(
                    domain="news",
                    state="data",
                    sources=_sources(
                        "fixture.news",
                        datetime(2026, 7, 24, 18, tzinfo=UTC),
                    ),
                    temporal_bases=("pit",),
                    evidence_refs=(first.ref,),
                ),
            ),
        ),
        evidence=(
            IncrementalEvidenceCandidate(evidence=first),
            IncrementalEvidenceCandidate(evidence=collided),
        ),
    )

    with pytest.raises(ValueError, match="collides with different final payloads"):
        normalize_incremental_collection(
            request,
            collected,
            sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
        )


def test_near_live_admission_preserves_retrieval_time_without_fabricating_availability() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24")
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="yfinance",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            content="Revenue growth remains positive.",
            origins=(
                EvidenceOrigin(
                    source="yfinance",
                    evidence_type="fundamentals_snapshot",
                    retrieved_at="2026-07-29T15:00:00Z",
                    temporal_scope=EvidenceTemporalScope.LIVE_ONLY,
                ),
            ),
        )
    )

    admitted = admit_incremental_observations(
        request,
        (candidate,),
        sealed_at=datetime(2026, 7, 29, 15, 1, tzinfo=UTC),
    )

    assert admitted == (candidate.evidence,)
    assert admitted[0].available_at is None
    assert admitted[0].origins[0].retrieved_at == "2026-07-29T15:00:00Z"


def test_live_only_observation_cannot_be_promoted_to_pit_by_available_at() -> None:
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="snapshot.vendor",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A retrieval-time snapshot with an invalid PIT claim.",
            origins=(
                EvidenceOrigin(
                    source="snapshot.vendor",
                    evidence_type="fundamentals_snapshot",
                    retrieved_at="2026-07-24T18:00:00Z",
                    temporal_scope=EvidenceTemporalScope.LIVE_ONLY,
                ),
            ),
        )
    )

    with pytest.raises(ValueError, match="live-only observations cannot claim PIT availability"):
        admit_incremental_observations(
            _collection_request(analysis_cutoff="2026-07-24"),
            (candidate,),
            sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
        )


def test_strict_pit_backfill_is_admitted_by_publication_not_effective_date() -> None:
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.filing",
            evidence_type="restatement",
            requested_date=date(2026, 7, 24),
            effective_date=date(2026, 6, 30),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A historical period was restated after the Full Baseline.",
        )
    )

    admitted = admit_incremental_observations(
        _collection_request(analysis_cutoff="2026-07-24"),
        (candidate,),
        sealed_at=datetime(2026, 7, 24, 19, tzinfo=UTC),
    )

    assert len(admitted) == 1
    assert admitted[0].effective_date == date(2026, 6, 30)
    assert admitted[0].available_at == datetime(2026, 7, 22, 12, tzinfo=UTC)


def test_near_live_admission_omits_six_day_snapshot_and_rejects_future_retrieval() -> None:
    request = _collection_request(analysis_cutoff="2026-07-23")
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="yfinance",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 23),
            content="Current snapshot.",
            origins=(
                EvidenceOrigin(
                    source="yfinance",
                    evidence_type="fundamentals_snapshot",
                    retrieved_at="2026-07-29T15:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )

    assert (
        admit_incremental_observations(
            request,
            (candidate,),
            sealed_at=datetime(2026, 7, 29, 15, 1, tzinfo=UTC),
        )
        == ()
    )
    with pytest.raises(ValueError, match="retrieval time cannot be after sealing"):
        admit_incremental_observations(
            _collection_request(analysis_cutoff="2026-07-24"),
            (candidate,),
            sealed_at=datetime(2026, 7, 29, 14, 59, tzinfo=UTC),
        )


def test_stock_performance_truncates_one_broader_adjusted_series() -> None:
    retrieved_at = datetime(2026, 7, 26, 12, tzinfo=UTC)
    series = MarketSeriesResult(
        instrument="NVDA",
        source="yfinance",
        adjustment_basis="vendor_adjusted_close",
        retrieved_at=retrieved_at,
        points=(
            MarketSeriesPoint(
                session="2026-07-17",
                completed_at="2026-07-17T20:00:00Z",
                adjusted_close=90,
            ),
            MarketSeriesPoint(
                session="2026-07-20",
                completed_at="2026-07-20T20:00:00Z",
                adjusted_close=100,
            ),
            MarketSeriesPoint(
                session="2026-07-24",
                completed_at="2026-07-24T20:00:00Z",
                adjusted_close=110,
            ),
            MarketSeriesPoint(
                session="2026-07-25",
                completed_at="2026-07-25T20:00:00Z",
                adjusted_close=111,
            ),
        ),
    )

    request = _collection_request(analysis_cutoff="2026-07-24")
    performance = calculate_stock_performance(request, series)

    assert performance.stock.status is PerformanceComponentStatus.CALCULATED
    assert performance.stock.calculation is not None
    assert performance.stock.calculation.start_session == date(2026, 7, 20)
    assert performance.stock.calculation.end_session == date(2026, 7, 24)
    assert performance.stock.calculation.baseline_information_cutoff_at == request.window_start
    assert performance.stock.calculation.target_information_cutoff_at == request.window_end
    assert performance.stock.calculation.start_value == 100
    assert performance.stock.calculation.end_value == 110
    assert performance.stock.calculation.unrounded_return == pytest.approx(0.1)
    assert performance.benchmarks == ()
    assert "points" not in performance.model_dump_json()


def test_performance_calculation_rejects_a_vendor_supplied_wrong_result() -> None:
    with pytest.raises(ValidationError, match="unrounded return must match endpoint values"):
        PerformanceCalculationRecord(
            provider="fixture.market",
            adjustment_basis="adjusted_close",
            retrieved_at=datetime(2026, 7, 24, 21, tzinfo=UTC),
            baseline_information_cutoff_at=datetime(2026, 7, 21, 3, 59, 59, tzinfo=UTC),
            target_information_cutoff_at=datetime(2026, 7, 25, 3, 59, 59, tzinfo=UTC),
            start_session=date(2026, 7, 20),
            end_session=date(2026, 7, 24),
            start_value=100,
            end_value=110,
            unrounded_return=0.5,
        )


def test_stock_performance_reports_not_yet_observable_for_one_selected_session() -> None:
    performance = calculate_stock_performance(
        _collection_request(analysis_cutoff="2026-07-24"),
        MarketSeriesResult(
            instrument="NVDA",
            source="fixture",
            adjustment_basis="adjusted_close",
            retrieved_at=datetime(2026, 7, 24, 23, tzinfo=UTC),
            points=(
                MarketSeriesPoint(
                    session="2026-07-20",
                    completed_at="2026-07-20T20:00:00Z",
                    adjusted_close=100,
                ),
            ),
        ),
    )

    assert performance.stock.status is PerformanceComponentStatus.NOT_YET_OBSERVABLE
    assert performance.stock.calculation is None


def test_stock_performance_excludes_a_session_completed_after_the_frozen_cutoff() -> None:
    request = _collection_request(analysis_cutoff="2026-07-24").model_copy(
        update={"window_start": datetime(2026, 7, 20, 18, tzinfo=UTC)}
    )

    performance = calculate_stock_performance(
        request,
        MarketSeriesResult(
            instrument="NVDA",
            source="fixture",
            adjustment_basis="adjusted_close",
            retrieved_at=datetime(2026, 7, 24, 23, tzinfo=UTC),
            points=(
                MarketSeriesPoint(
                    session="2026-07-17",
                    completed_at="2026-07-17T20:00:00Z",
                    adjusted_close=90,
                ),
                MarketSeriesPoint(
                    session="2026-07-20",
                    completed_at="2026-07-20T20:00:00Z",
                    adjusted_close=100,
                ),
                MarketSeriesPoint(
                    session="2026-07-24",
                    completed_at="2026-07-24T20:00:00Z",
                    adjusted_close=110,
                ),
            ),
        ),
    )

    assert performance.stock.calculation is not None
    assert performance.stock.calculation.start_session == date(2026, 7, 17)


def test_retrieval_time_only_refresh_does_not_advance_information() -> None:
    baseline = _near_live_item(
        retrieved_at="2026-07-24T15:00:00Z",
        content="Stable snapshot.",
    )
    refreshed = _near_live_item(
        retrieved_at="2026-07-25T15:00:00Z",
        content="Stable snapshot.",
    )
    changed = _near_live_item(
        retrieved_at="2026-07-25T15:00:00Z",
        content="Changed snapshot.",
    )
    unavailable_performance = calculate_stock_performance(
        _collection_request(analysis_cutoff="2026-07-24"),
        None,
    )

    assert assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(refreshed,),
        performance=unavailable_performance,
        stock_series_admitted=False,
    ) == InformationAdvancement(advanced=False)
    advanced = assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(changed,),
        performance=unavailable_performance,
        stock_series_admitted=False,
    )
    assert advanced.advanced is True
    assert advanced.reasons == ("admissible_observation",)
    assert len(advanced.observation_ids) == 1


def test_source_fallback_change_alone_does_not_advance_information() -> None:
    baseline = _near_live_item(
        retrieved_at="2026-07-24T15:00:00Z",
        content="Stable snapshot.",
        source="primary.vendor",
    )
    fallback = _near_live_item(
        retrieved_at="2026-07-25T15:00:00Z",
        content="Stable snapshot.",
        source="fallback.vendor",
    )

    advancement = assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(fallback,),
        performance=calculate_stock_performance(
            _collection_request(analysis_cutoff="2026-07-24"),
            None,
        ),
        stock_series_admitted=False,
    )

    assert advancement == InformationAdvancement(advanced=False)


def test_distinct_observation_types_with_the_same_payload_advance_information() -> None:
    baseline = _near_live_item(
        retrieved_at="2026-07-24T15:00:00Z",
        content="Neutral",
        evidence_type="rating_snapshot",
    )
    current = _near_live_item(
        retrieved_at="2026-07-25T15:00:00Z",
        content="Neutral",
        evidence_type="fundamentals_snapshot",
    )

    advancement = assess_information_advancement(
        baseline_items=(baseline,),
        current_items=(current,),
        performance=calculate_stock_performance(
            _collection_request(analysis_cutoff="2026-07-24"),
            None,
        ),
        stock_series_admitted=False,
    )

    assert advancement.advanced is True
    assert advancement.reasons == ("admissible_observation",)


def _near_live_item(
    *,
    retrieved_at: str,
    content: str,
    source: str = "yfinance",
    evidence_type: str = "fundamentals_snapshot",
) -> EvidenceItem:
    return EvidenceItem.create(
        source=source,
        evidence_type=evidence_type,
        requested_date=date(2026, 7, 24),
        content=content,
        origins=(
            EvidenceOrigin(
                source=source,
                evidence_type=evidence_type,
                retrieved_at=retrieved_at,
                temporal_scope="live_only",
            ),
        ),
    )


def _collection_request(*, analysis_cutoff: str) -> IncrementalCollectionRequest:
    return IncrementalCollectionRequest(
        version="1",
        instrument="NVDA",
        market="united_states",
        route_suffix="",
        baseline_analysis_cutoff="2026-07-20",
        analysis_cutoff=analysis_cutoff,
        window_start=datetime(2026, 7, 21, 3, 59, 59, tzinfo=UTC),
        window_end=datetime(2026, 7, 25, 3, 59, 59, tzinfo=UTC),
        enabled_domains=("fundamentals", "market", "news", "social"),
        configured_routes={"data_vendors": {"fundamentals": "yfinance"}},
        near_live_max_age_days=5,
    )


def _sources(
    source: str,
    retrieved_at: datetime,
    *,
    fallback: bool = False,
) -> tuple[CollectionSourceProvenance, ...]:
    return (
        CollectionSourceProvenance(
            source=source,
            fallback=fallback,
            retrieved_at=retrieved_at,
        ),
    )
