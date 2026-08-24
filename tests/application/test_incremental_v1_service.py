from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tests.application.test_service import _equity_resolver, _Graph, _service
from tradingagents.application.contracts import (
    AnalysisRequest,
    BenchmarkContext,
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionSummary,
    EvidenceItem,
    EvidenceOrigin,
    FullResearchRequiredReason,
    IncrementalCollectionRequest,
    IncrementalCollectionResult,
    IncrementalEvidenceCandidate,
    MarketSeriesPoint,
    MarketSeriesResult,
    PerformanceCalculationRecord,
    PerformanceComponent,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    ResearchNodeRecord,
    RunEvidenceRecord,
)
from tradingagents.application.errors import (
    InvalidIncrementalBaselineError,
    NoInformationAdvancementError,
)
from tradingagents.application.repository import EvidenceConflictError
from tradingagents.application.service import AnalysisService, default_incremental_synthesizer


def _unavailable_domains(request: IncrementalCollectionRequest):
    return tuple(
        CollectionDomainResult(
            domain=domain,
            state="unavailable",
            diagnostic=CollectionDiagnostic(code="not_configured"),
        )
        for domain in request.enabled_domains
    )


def _incremental_service(
    app_settings,
    repository,
    *,
    collector,
    synthesizer=default_incremental_synthesizer,
    now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collector,
        incremental_synthesizer=synthesizer,
        now=now,
    )


def _pit_collection(
    request: IncrementalCollectionRequest,
    candidate: IncrementalEvidenceCandidate,
    *,
    domain: str = "news",
) -> IncrementalCollectionResult:
    domains = list(_unavailable_domains(request))
    index = request.enabled_domains.index(domain)
    domains[index] = CollectionDomainResult(
        domain=domain,
        state="data",
        source=candidate.evidence.source,
        fallback=candidate.evidence.fallback,
        retrieved_at=request.window_end,
        temporal_bases=("pit",),
        evidence_refs=(candidate.evidence.ref,),
    )
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version,
            market=request.market,
            domains=tuple(domains),
        ),
        evidence=(candidate,),
    )


def test_incremental_service_commits_simplified_actual_result_products(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A newly published filing.",
        )
    )
    synthesis_inputs = []

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = []
        for domain in request.enabled_domains:
            if domain == "news":
                domains.append(
                    CollectionDomainResult(
                        domain="news",
                        state="data",
                        source="fixture.news",
                        retrieved_at=request.window_end,
                        temporal_bases=("pit",),
                        evidence_refs=(candidate.evidence.ref,),
                    )
                )
            else:
                domains.append(
                    CollectionDomainResult(
                        domain=domain,
                        state="unavailable",
                        diagnostic=CollectionDiagnostic(code="not_configured"),
                    )
                )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(candidate,),
        )

    def synthesize(input_):
        synthesis_inputs.append(input_)
        return default_incremental_synthesizer(input_)

    result = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        synthesizer=synthesize,
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    node = next(item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id)
    assert node.collection_summary is not None
    assert {item.domain: item.state.value for item in node.collection_summary.domains} == {
        "fundamentals": "unavailable",
        "market": "unavailable",
        "news": "data",
        "social": "unavailable",
    }
    assert {
        item.domain: item.status.value for item in node.research_availability.domains
    } == {
        "fundamentals": "missing",
        "market": "missing",
        "news": "available",
        "social": "missing",
    }
    assert node.information_advancement.reasons == ("admissible_observation",)
    assert node.performance.stock.status.value == "unavailable"
    assert node.reassessment is not None
    assert node.decision is not None
    assert len(synthesis_inputs) == 1
    assert not hasattr(synthesis_inputs[0], "outcome_review_status")
    assert result.metrics.llm_calls == 0


def test_incremental_service_rejects_no_information_advancement_before_synthesis(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    synthesized = []

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=_unavailable_domains(request),
            )
        )

    with pytest.raises(NoInformationAdvancementError):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
            synthesizer=lambda input_: synthesized.append(input_),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert synthesized == []
    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (
        baseline.run_id,
    )
    assert any(
        event.event_type == "incremental.no_advancement"
        for event in repository.list_events(failed[0].id)
    )


def test_completed_stock_session_advances_and_persists_one_sealed_calculation(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=_unavailable_domains(request),
            ),
            stock_series=MarketSeriesResult(
                instrument=request.instrument,
                source="fixture.market",
                adjustment_basis="adjusted_close",
                retrieved_at=datetime(2026, 7, 24, 21, tzinfo=UTC),
                points=(
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

    result = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 24, 22, tzinfo=UTC),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    node = next(item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id)
    assert node.information_advancement.reasons == ("completed_stock_session",)
    calculation = node.performance.stock.calculation
    assert calculation is not None
    assert calculation.start_session == date(2026, 7, 20)
    assert calculation.end_session == date(2026, 7, 24)
    assert calculation.unrounded_return == pytest.approx(0.1)


def test_incremental_service_rejects_benchmark_from_another_frozen_interval(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        calculation = PerformanceCalculationRecord(
            provider="fixture.benchmark",
            adjustment_basis="adjusted_close",
            retrieved_at=datetime(2026, 7, 24, 21, tzinfo=UTC),
            baseline_information_cutoff_at=datetime(2026, 7, 18, 20, tzinfo=UTC),
            target_information_cutoff_at=datetime(2026, 7, 24, 20, tzinfo=UTC),
            start_session=date(2026, 7, 17),
            end_session=date(2026, 7, 24),
            start_value=100,
            end_value=105,
            unrounded_return=0.05,
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=_unavailable_domains(request),
            ),
            benchmarks=(
                BenchmarkContext(
                    name="S&P 500",
                    component=PerformanceComponent(
                        status="calculated",
                        calculation=calculation,
                    ),
                ),
            ),
        )

    with pytest.raises(ValueError, match="benchmark cutoffs must match the frozen request"):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
            now=lambda: datetime(2026, 7, 24, 22, tzinfo=UTC),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (
        baseline.run_id,
    )


def test_near_live_five_day_observation_is_admitted_without_claiming_pit(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.snapshot",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            content="Bounded current snapshot.",
            origins=(
                EvidenceOrigin(
                    source="fixture.snapshot",
                    evidence_type="fundamentals_snapshot",
                    retrieved_at="2026-07-29T15:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        fundamentals = request.enabled_domains.index("fundamentals")
        domains[fundamentals] = CollectionDomainResult(
            domain="fundamentals",
            state="data",
            source="fixture.snapshot",
            retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
            temporal_bases=("near_live_advisory",),
            evidence_refs=(candidate.evidence.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(candidate,),
        )

    result = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 29, 15, 1, tzinfo=UTC),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    node = next(item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id)
    fundamentals = next(
        item for item in node.collection_summary.domains if item.domain == "fundamentals"
    )
    assert fundamentals.temporal_bases == ("near_live_advisory",)
    assert result.evidence.items[0].available_at is None
    assert result.evidence.items[0].origins[0].retrieved_at == "2026-07-29T15:00:00Z"


def test_incremental_service_persists_bounded_best_effort_collection_states(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    partial = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.snapshot",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            content="A bounded current fundamentals snapshot.",
            origins=(
                EvidenceOrigin(
                    source="fixture.snapshot",
                    evidence_type="fundamentals_snapshot",
                    retrieved_at="2026-07-29T15:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )
    fallback = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fallback.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A filing observed through the configured fallback.",
            fallback=True,
        )
    )
    stale = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.social",
            evidence_type="social_snapshot",
            requested_date=date(2026, 7, 24),
            content="A six-day snapshot that must be omitted.",
            origins=(
                EvidenceOrigin(
                    source="fixture.social",
                    evidence_type="social_snapshot",
                    retrieved_at="2026-07-30T15:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = {
            "fundamentals": CollectionDomainResult(
                domain="fundamentals",
                state="partial",
                source="fixture.snapshot",
                retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
                temporal_bases=("near_live_advisory",),
                evidence_refs=(partial.evidence.ref,),
                diagnostic=CollectionDiagnostic(code="bounded_snapshot"),
            ),
            "market": CollectionDomainResult(
                domain="market",
                state="unavailable",
                source="fixture.market",
                retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
                diagnostic=CollectionDiagnostic(code="provider_failure"),
            ),
            "news": CollectionDomainResult(
                domain="news",
                state="data",
                source="fallback.news",
                fallback=True,
                retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
                temporal_bases=("pit",),
                evidence_refs=(fallback.evidence.ref,),
            ),
            "social": CollectionDomainResult(
                domain="social",
                state="data",
                source="fixture.social",
                retrieved_at=datetime(2026, 7, 30, 15, tzinfo=UTC),
                temporal_bases=("near_live_advisory",),
                evidence_refs=(stale.evidence.ref,),
            ),
        }
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains[domain] for domain in request.enabled_domains),
            ),
            evidence=(partial, fallback, stale),
            stock_series=MarketSeriesResult(
                instrument=request.instrument,
                source="fixture.market",
                adjustment_basis="adjusted_close",
                retrieved_at=datetime(2026, 7, 29, 15, tzinfo=UTC),
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
                        adjusted_close=120,
                    ),
                ),
            ),
        )

    result = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 30, 15, 1, tzinfo=UTC),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    node = next(
        item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id
    )
    domains = {item.domain: item for item in node.collection_summary.domains}
    assert domains["fundamentals"].state.value == "partial"
    assert domains["market"].diagnostic.code == "provider_failure"
    assert domains["news"].fallback is True
    assert domains["social"].state.value == "empty"
    assert domains["social"].diagnostic.code == "outside_temporal_boundary"
    assert {item.ref for item in result.evidence.items} == {
        partial.evidence.ref,
        fallback.evidence.ref,
    }
    assert {
        item.domain: item.status.value for item in node.research_availability.domains
    } == {
        "fundamentals": "limited",
        "market": "missing",
        "news": "available",
        "social": "missing",
    }
    calculation = node.performance.stock.calculation
    assert calculation is not None
    assert calculation.start_session == date(2026, 7, 20)
    assert calculation.end_session == date(2026, 7, 24)


def test_incremental_atomic_commit_failure_keeps_only_the_full_baseline(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="New filing.",
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        news = request.enabled_domains.index("news")
        domains[news] = CollectionDomainResult(
            domain="news",
            state="data",
            source="fixture.news",
            retrieved_at=request.window_end,
            temporal_bases=("pit",),
            evidence_refs=(candidate.evidence.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(candidate,),
        )

    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER fail_incremental_node BEFORE INSERT ON research_nodes
            WHEN NEW.research_kind = 'incremental'
            BEGIN SELECT RAISE(ABORT, 'injected incremental failure'); END
            """
        )

    with pytest.raises(Exception, match="injected incremental failure"):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (
        baseline.run_id,
    )
    with repository.sessions() as session:
        assert session.query(DecisionRecord).count() == 1
        assert session.query(ResearchNodeRecord).count() == 1
        assert session.query(RunEvidenceRecord).count() == 1


def test_incremental_synthesis_excludes_sibling_evidence_from_its_reference_closure(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        available_at = datetime(
            2026,
            7,
            22 if request.analysis_cutoff == date(2026, 7, 24) else 23,
            12,
            tzinfo=UTC,
        )
        candidate = IncrementalEvidenceCandidate(
            evidence=EvidenceItem.create(
                source="fixture.news",
                evidence_type="filing",
                requested_date=request.analysis_cutoff,
                available_at=available_at,
                content=f"Filing observed for {request.analysis_cutoff.isoformat()}.",
            )
        )
        domains = list(_unavailable_domains(request))
        news = request.enabled_domains.index("news")
        domains[news] = CollectionDomainResult(
            domain="news",
            state="data",
            source="fixture.news",
            retrieved_at=request.window_end,
            temporal_bases=("pit",),
            evidence_refs=(candidate.evidence.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(candidate,),
        )

    first = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 26, 12, tzinfo=UTC),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )
    sibling_ref = first.evidence.items[0].ref
    synthesis_inputs = []

    def synthesize(input_):
        synthesis_inputs.append(input_)
        synthesis = default_incremental_synthesizer(input_)
        return synthesis.model_copy(
            update={
                "decision": synthesis.decision.model_copy(
                    update={"evidence_refs": (sibling_ref,)}
                )
            }
        )

    with pytest.raises(ValueError, match="only the Full Baseline or current Evidence"):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
            synthesizer=synthesize,
            now=lambda: datetime(2026, 7, 26, 12, tzinfo=UTC),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 25),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert len(synthesis_inputs) == 1
    assert sibling_ref not in synthesis_inputs[0].permitted_baseline_evidence_refs
    assert sibling_ref not in {
        item.ref for item in synthesis_inputs[0].incremental_evidence.items
    }
    assert {node.id for node in repository.get_timeline("NVDA").nodes} == {
        baseline.run_id,
        first.run_id,
    }


def test_incremental_service_rejects_copying_a_full_baseline_evidence_reference(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_item = repository.get_evidence(baseline.run_id).items[0]
    copied = baseline_item.model_copy(
        update={
            "available_at": None,
            "origins": (
                EvidenceOrigin(
                    source=baseline_item.source,
                    evidence_type=baseline_item.evidence_type,
                    retrieved_at="2026-07-24T18:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        }
    )

    with pytest.raises(EvidenceConflictError, match="must not copy Full Baseline"):
        _incremental_service(
            app_settings,
            repository,
            collector=lambda request: _pit_collection(
                request,
                IncrementalEvidenceCandidate(evidence=copied),
            ),
            now=lambda: datetime(2026, 7, 24, 19, tzinfo=UTC),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (
        baseline.run_id,
    )


@pytest.mark.parametrize("mutation_phase", ["collection", "synthesis"])
def test_incremental_commit_revalidates_a_baseline_trashed_during_execution(
    app_settings,
    repository,
    mutation_phase,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="Baseline mutation race.",
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        if mutation_phase == "collection":
            repository.trash_runs((baseline.run_id,))
        return _pit_collection(request, candidate)

    def synthesize(input_):
        if mutation_phase == "synthesis":
            repository.trash_runs((baseline.run_id,))
        return default_incremental_synthesizer(input_)

    with pytest.raises(InvalidIncrementalBaselineError):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
            synthesizer=synthesize,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items
    assert len(failed) == 1
    assert repository.evidence_status(failed[0].id).status == "pending"
    with repository.sessions() as session:
        assert session.get(DecisionRecord, failed[0].id) is None
        assert session.get(ResearchNodeRecord, failed[0].id) is None


@pytest.mark.parametrize("warning_has_dangling_ref", [False, True])
def test_full_research_required_warning_allows_no_ref_but_rejects_a_dangling_ref(
    app_settings,
    repository,
    warning_has_dangling_ref,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="Potential attribution change.",
        )
    )

    def synthesize(input_):
        synthesis = default_incremental_synthesizer(input_)
        return synthesis.model_copy(
            update={
                "full_research_required_reasons": (
                    FullResearchRequiredReason(
                        code="attribution.unresolved",
                        message="The bounded update cannot resolve attribution.",
                        origin="semantic",
                        evidence_refs=("ev_000000000000",)
                        if warning_has_dangling_ref
                        else (),
                    ),
                )
            }
        )

    service = _incremental_service(
        app_settings,
        repository,
        collector=lambda request: _pit_collection(request, candidate),
        synthesizer=synthesize,
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    if warning_has_dangling_ref:
        with pytest.raises(ValueError, match="must close over the baseline or current bundle"):
            service.run(request)
    else:
        result = service.run(request)
        node = next(
            item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id
        )
        assert node.full_research_required_reasons[0].evidence_refs == ()
