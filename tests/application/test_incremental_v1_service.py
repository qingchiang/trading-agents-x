from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from tests.application.test_service import _equity_resolver, _Graph, _service
from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    BenchmarkSeriesResult,
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionSourceProvenance,
    CollectionSummary,
    EvidenceBundle,
    EvidenceItem,
    EvidenceOrigin,
    FullResearchRequiredReason,
    IncrementalCollectionRequest,
    IncrementalCollectionResult,
    IncrementalEvidenceCandidate,
    MarketSeriesPoint,
    MarketSeriesResult,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    ResearchNodeRecord,
    RunEvidenceRecord,
    RunRecord,
)
from tradingagents.application.errors import (
    InvalidIncrementalBaselineError,
    NoInformationAdvancementError,
    UnsupportedInstrumentError,
)
from tradingagents.application.llms import RunLLMs
from tradingagents.application.repository import EvidenceConflictError
from tradingagents.application.service import AnalysisService, default_incremental_synthesizer
from tradingagents.dataflows.config import get_config
from tradingagents.graph.research_graph import GraphExecution


def _unavailable_domains(request: IncrementalCollectionRequest):
    return tuple(
        CollectionDomainResult(
            domain=domain,
            state="unavailable",
            diagnostic=CollectionDiagnostic(code="not_configured"),
        )
        for domain in request.enabled_domains
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


def _incremental_service(
    app_settings,
    repository,
    *,
    collector,
    synthesizer=default_incremental_synthesizer,
    eligibility_resolver=_equity_resolver,
    now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=eligibility_resolver,
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
        sources=_sources(
            candidate.evidence.source,
            request.window_end,
            fallback=candidate.evidence.fallback,
        ),
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


def test_real_full_social_observation_does_not_advance_for_incremental_retrieval_spelling(
    app_settings,
    repository,
) -> None:
    content = "Bullish: 1 (100%)\n\n[2026-07-20 12:00:00 EDT · @user · Bullish] same post"

    class FullSocialGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, **_kwargs):
            baseline_item = EvidenceItem.create(
                source="StockTwits",
                evidence_type="retail social messages",
                requested_date=date(2026, 7, 20),
                content=content,
                origins=(
                    EvidenceOrigin(
                        source="StockTwits",
                        evidence_type="retail social messages",
                        effective="2026-07-13 to 2026-07-20",
                        timing="live source; market-calendar window filtered",
                        retrieved_at="2026-07-20T20:00:00Z",
                        temporal_scope="live_only",
                    ),
                ),
            )
            bundle = EvidenceBundle(
                instrument=context.request.ticker,
                analysis_date=context.request.analysis_date,
                items=(baseline_item,),
            )
            report = analyst_report(analyst="social", evidence_ref=baseline_item.ref)
            decision = research_decision(evidence_refs=(baseline_item.ref,))
            return GraphExecution(
                state={}, evidence=bundle, reports={"social": report}, decision=decision
            )

    baseline = _service(app_settings, repository, graph_factory=FullSocialGraph).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="stocktwits",
            evidence_type="social_snapshot",
            requested_date=date(2026, 7, 24),
            content=content,
            origins=(
                EvidenceOrigin(
                    source="stocktwits",
                    evidence_type="social_snapshot",
                    timing="live retrieval-time snapshot",
                    retrieved_at="2026-07-24T20:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        domains[request.enabled_domains.index("social")] = CollectionDomainResult(
            domain="social",
            state="partial",
            sources=_sources("stocktwits", datetime(2026, 7, 24, 20, tzinfo=UTC)),
            temporal_bases=("near_live_advisory",),
            evidence_refs=(candidate.evidence.ref,),
            diagnostic=CollectionDiagnostic(code="bounded_current_social_feed"),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version, market=request.market, domains=tuple(domains)
            ),
            evidence=(candidate,),
        )

    synthesis_inputs = []
    service = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        synthesizer=lambda input_: synthesis_inputs.append(input_),
    )
    with pytest.raises(NoInformationAdvancementError):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert synthesis_inputs == []
    assert repository.list_runs(status=RunStatus.FAILED).items[0].id != baseline.run_id
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


def _full_fundamentals_graph(content: str):
    class FullFundamentalsGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, **_kwargs):
            baseline_item = EvidenceItem.create(
                source="yfinance",
                evidence_type="get_fundamentals",
                requested_date=context.request.analysis_date,
                content=content,
                origins=(
                    EvidenceOrigin(
                        source="yfinance",
                        evidence_type="get_fundamentals",
                        timing="legacy live .info retrieval",
                        retrieved_at="2026-07-20T20:00:00Z",
                        temporal_scope="live_only",
                    ),
                ),
            )
            bundle = EvidenceBundle(
                instrument=context.request.ticker,
                analysis_date=context.request.analysis_date,
                items=(baseline_item,),
            )
            report = analyst_report(analyst="fundamentals", evidence_ref=baseline_item.ref)
            decision = research_decision(evidence_refs=(baseline_item.ref,))
            return GraphExecution(
                state={}, evidence=bundle, reports={"fundamentals": report}, decision=decision
            )

    return FullFundamentalsGraph


def _fundamentals_collection(
    request: IncrementalCollectionRequest,
    candidate: IncrementalEvidenceCandidate,
) -> IncrementalCollectionResult:
    domains = list(_unavailable_domains(request))
    domains[request.enabled_domains.index("fundamentals")] = CollectionDomainResult(
        domain="fundamentals",
        state="partial",
        sources=_sources("yfinance", datetime(2026, 7, 24, 20, tzinfo=UTC)),
        temporal_bases=("near_live_advisory",),
        evidence_refs=(candidate.evidence.ref,),
        diagnostic=CollectionDiagnostic(code="near_live_snapshot"),
    )
    return IncrementalCollectionResult(
        collection_summary=CollectionSummary(
            version=request.version, market=request.market, domains=tuple(domains)
        ),
        evidence=(candidate,),
    )


def test_real_full_fundamentals_observation_does_not_advance_for_retrieval_headers_or_type(
    app_settings,
    repository,
) -> None:
    baseline_content = """# Company Fundamentals for NVDA (live yfinance snapshot)
# Requested analysis date: 2026-07-20
# Retrieved at: 2026-07-20 20:00:00
# Not point-in-time historical data.

Market Cap: 123
PE Ratio (TTM): 42"""
    current_content = """# Company Fundamentals for NVDA (live yfinance snapshot)
# Requested analysis date: 2026-07-24
# Retrieved at: 2026-07-24 20:00:00
# Not point-in-time historical data.

Market Cap: 123
PE Ratio (TTM): 42"""
    baseline = _service(
        app_settings, repository, graph_factory=_full_fundamentals_graph(baseline_content)
    ).run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="yfinance",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            content=current_content,
            origins=(
                EvidenceOrigin(
                    source="yfinance",
                    evidence_type="fundamentals_snapshot",
                    timing="live retrieval-time snapshot",
                    retrieved_at="2026-07-24T20:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )
    synthesis_inputs = []
    service = _incremental_service(
        app_settings,
        repository,
        collector=lambda request: _fundamentals_collection(request, candidate),
        synthesizer=lambda input_: synthesis_inputs.append(input_),
    )

    with pytest.raises(NoInformationAdvancementError):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert synthesis_inputs == []
    assert repository.list_runs(status=RunStatus.FAILED).items[0].id != baseline.run_id
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


def test_real_full_fundamentals_field_change_advances_information(
    app_settings,
    repository,
) -> None:
    baseline_content = """# Company Fundamentals for NVDA (live yfinance snapshot)
# Requested analysis date: 2026-07-20
# Retrieved at: 2026-07-20 20:00:00

Market Cap: 123"""
    changed_content = """# Company Fundamentals for NVDA (live yfinance snapshot)
# Requested analysis date: 2026-07-24
# Retrieved at: 2026-07-24 20:00:00

Market Cap: 456"""
    baseline = _service(
        app_settings, repository, graph_factory=_full_fundamentals_graph(baseline_content)
    ).run(AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20)))
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="yfinance",
            evidence_type="fundamentals_snapshot",
            requested_date=date(2026, 7, 24),
            content=changed_content,
            origins=(
                EvidenceOrigin(
                    source="yfinance",
                    evidence_type="fundamentals_snapshot",
                    timing="live retrieval-time snapshot",
                    retrieved_at="2026-07-24T20:00:00Z",
                    temporal_scope="live_only",
                ),
            ),
        )
    )
    synthesis_inputs = []

    def synthesize(input_):
        synthesis_inputs.append(input_)
        return default_incremental_synthesizer(input_)

    result = _incremental_service(
        app_settings,
        repository,
        collector=lambda request: _fundamentals_collection(request, candidate),
        synthesizer=synthesize,
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    assert len(synthesis_inputs) == 1
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (
        baseline.run_id,
        result.run_id,
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
                        sources=_sources("fixture.news", request.window_end),
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
    assert {item.domain: item.status.value for item in node.research_availability.domains} == {
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


def test_truncated_incremental_synthesis_commits_via_sectioned_recovery(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_decision = repository.get_result(baseline.run_id).decision
    assert baseline_decision is not None
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A newly published filing.",
        )
    )
    component_ids = (
        "executive_summary",
        "thesis",
        "risks.0",
        "invalidation_conditions.0",
        "scenarios.base.outcome",
        "scenarios.base.core_assumptions.0",
        "scenarios.bull.outcome",
        "scenarios.bull.core_assumptions.0",
        "scenarios.bear.outcome",
        "scenarios.bear.core_assumptions.0",
    )

    class _Invoker:
        def __init__(self, response):
            self.response = response

        def invoke(self, _prompt, config=None):
            del config
            if isinstance(self.response, BaseException):
                raise self.response
            return self.response

    class _SemanticLLM:
        def invoke(self, _prompt, config=None):
            del config
            return AIMessage(content="The bounded update reaffirms the baseline.")

    class _SerializerLLM:
        preferred_structured_output_method = "function_calling"
        structured_output_max_tokens = 16_384

        def with_structured_output(
            self,
            schema,
            *,
            method=None,
            include_raw=False,
            **_kwargs,
        ):
            assert include_raw is True
            if schema.__name__ == "IncrementalSynthesis":
                if method == "json_mode":
                    return _Invoker(RuntimeError("monolithic repair unavailable"))
                return _Invoker(
                    {
                        "raw": AIMessage(
                            content="",
                            response_metadata={"finish_reason": "length"},
                        ),
                        "parsed": None,
                        "parsing_error": ValueError("truncated"),
                    }
                )
            if schema.__name__ == "_IncrementalReassessmentSection":
                parsed = {
                    "reassessment": {
                        "entries": [
                            {
                                "component_id": component_id,
                                "disposition": "reaffirmed",
                                "reason": "The bounded update does not change this component.",
                            }
                            for component_id in component_ids
                        ]
                    },
                    "full_research_required_reasons": [],
                }
            elif schema.__name__ == "_IncrementalDecisionSection":
                parsed = {"decision": baseline_decision.model_dump(mode="json")}
            else:
                raise AssertionError(f"unexpected schema: {schema.__name__}")
            return _Invoker(
                {
                    "raw": AIMessage(content=""),
                    "parsed": parsed,
                    "parsing_error": None,
                }
            )

    serializer = _SerializerLLM()
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: RunLLMs(
            quick=_SemanticLLM(),
            deep=_SemanticLLM(),
            quick_serializer=serializer,
            deep_serializer=serializer,
        ),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda request: _pit_collection(request, candidate),
        incremental_synthesizer=None,
        now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    node = repository.get_timeline("NVDA").nodes[-1]
    assert len(node.reassessment.entries) == len(component_ids)
    assert any(
        event.event_type == "node.output_recovered"
        and event.payload["method"] == "sectioned_recovery"
        for event in repository.list_events(result.run_id)
    )


def test_incremental_collector_uses_the_frozen_run_dataflow_configuration(
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
            content="A filing collected under the frozen Run configuration.",
        )
    )
    observed = []

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        observed.append((get_config(), request))
        return _pit_collection(request, candidate)

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

    active_config, collection_request = observed[0]
    assert active_config["data_vendors"] == dict(
        collection_request.configured_routes["data_vendors"]
    )


def test_incremental_service_revalidates_eligibility_immediately_before_commit(
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
            content="A filing collected before eligibility changed.",
        )
    )
    calls = 0

    def eligibility(symbol: str):
        nonlocal calls
        calls += 1
        return {
            "symbol": symbol,
            "quote_type": "ETF" if calls == 3 else "EQUITY",
        }

    with pytest.raises(UnsupportedInstrumentError):
        _incremental_service(
            app_settings,
            repository,
            collector=lambda request: _pit_collection(request, candidate),
            eligibility_resolver=eligibility,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert calls == 3
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


def test_incremental_retry_uses_the_retained_run_dataflow_configuration(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    observed_news_routes = []

    def eligibility(symbol: str):
        observed_news_routes.append(get_config()["data_vendors"]["news_data"])
        return {"symbol": symbol, "quote_type": "EQUITY"}

    service = _incremental_service(
        app_settings,
        repository,
        collector=lambda request: IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=_unavailable_domains(request),
            )
        ),
        eligibility_resolver=eligibility,
    )
    with pytest.raises(NoInformationAdvancementError):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed = repository.list_runs(status=RunStatus.FAILED).items[0]
    with repository.sessions() as session:
        record = session.get(RunRecord, failed.id)
        assert record is not None
        retained_config = deepcopy(record.config_json)
        retained_config["data_config"]["data_vendors"]["news_data"] = "frozen_retry_fixture"
        record.config_json = retained_config
        session.commit()
    observed_news_routes.clear()

    retried = service.retry(failed.id)

    assert retried.id == failed.id
    assert observed_news_routes == ["frozen_retry_fixture"]


def test_incremental_commit_revalidates_baseline_schema_after_synthesis(
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
            content="A filing collected before the baseline schema changed.",
        )
    )

    def synthesize(input_):
        with repository.sessions() as session:
            record = session.get(RunRecord, baseline.run_id)
            assert record is not None
            record.research_schema_version = "obsolete"
            session.commit()
        return default_incremental_synthesizer(input_)

    with pytest.raises(
        InvalidIncrementalBaselineError,
        match="incompatible Research Schema Version at commit",
    ):
        _incremental_service(
            app_settings,
            repository,
            collector=lambda request: _pit_collection(request, candidate),
            synthesizer=synthesize,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


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
    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)
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
    market_evidence = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.market",
            evidence_type="adjusted_close",
            requested_date=date(2026, 7, 24),
            effective_date=date(2026, 7, 24),
            value=110,
            content="The completed 2026-07-24 adjusted close.",
            fallback=True,
        ),
        available_on=date(2026, 7, 24),
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        market = request.enabled_domains.index("market")
        domains[market] = CollectionDomainResult(
            domain="market",
            state="data",
            sources=_sources(
                "fixture.market",
                datetime(2026, 7, 24, 21, tzinfo=UTC),
                fallback=True,
            ),
            temporal_bases=("pit",),
            evidence_refs=(market_evidence.evidence.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(market_evidence,),
            stock_series=MarketSeriesResult(
                instrument=request.instrument,
                source="fixture.market",
                fallback=True,
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
            stock_series_evidence_ref=market_evidence.evidence.ref,
            benchmark_series=(
                BenchmarkSeriesResult(
                    name="S&P 500",
                    series=MarketSeriesResult(
                        instrument="^GSPC",
                        source="fixture.benchmark",
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
                                adjusted_close=105,
                            ),
                        ),
                    ),
                ),
            ),
        )

    result = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 25, 5, tzinfo=UTC),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    node = next(item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id)
    assert node.information_advancement.reasons == (
        "admissible_observation",
        "completed_stock_session",
    )
    calculation = node.performance.stock.calculation
    assert calculation is not None
    assert calculation.start_session == date(2026, 7, 20)
    assert calculation.end_session == date(2026, 7, 24)
    assert calculation.unrounded_return == pytest.approx(0.1)
    assert calculation.fallback is True
    benchmark = node.performance.benchmarks[0]
    assert benchmark.component.calculation is not None
    assert benchmark.component.calculation.unrounded_return == pytest.approx(0.05)
    assert benchmark.reported_difference == pytest.approx(0.05)


def test_completed_stock_session_rejects_unrelated_market_evidence(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    unrelated = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.market",
            evidence_type="technical_indicator",
            requested_date=date(2026, 7, 24),
            effective_date=date(2026, 7, 24),
            value=110,
            content="An unrelated indicator from the same retrieval.",
        ),
        available_on=date(2026, 7, 24),
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        market = request.enabled_domains.index("market")
        domains[market] = CollectionDomainResult(
            domain="market",
            state="data",
            sources=_sources(
                "fixture.market",
                datetime(2026, 7, 24, 21, tzinfo=UTC),
            ),
            temporal_bases=("pit",),
            evidence_refs=(unrelated.evidence.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(unrelated,),
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
            stock_series_evidence_ref=unrelated.evidence.ref,
        )

    with pytest.raises(
        ValueError,
        match="stock series advancement requires admitted current market Evidence",
    ):
        _incremental_service(
            app_settings,
            repository,
            collector=collect,
            now=lambda: datetime(2026, 7, 25, 5, tzinfo=UTC),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )


def test_incremental_service_rejects_unadmitted_stock_series_advancement(
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

    with pytest.raises(
        ValueError,
        match="stock series advancement requires admitted current market Evidence",
    ):
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


def test_incremental_service_calculates_benchmark_from_its_actual_series(
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
            content="A new filing that advances the bounded update.",
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        return _pit_collection(request, candidate).model_copy(
            update={
                "benchmark_series": (
                    BenchmarkSeriesResult(
                        name="S&P 500",
                        series=MarketSeriesResult(
                            instrument="^GSPC",
                            source="fixture.benchmark",
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
                                    adjusted_close=105,
                                ),
                            ),
                        ),
                    ),
                ),
            }
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
    calculation = node.performance.benchmarks[0].component.calculation
    assert calculation is not None
    assert calculation.start_session == date(2026, 7, 20)
    assert calculation.end_session == date(2026, 7, 24)
    assert calculation.unrounded_return == pytest.approx(0.05)
    assert node.performance.benchmarks[0].reported_difference is None


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
            sources=_sources(
                "fixture.snapshot",
                datetime(2026, 7, 29, 15, tzinfo=UTC),
            ),
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
    market_evidence = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.market",
            evidence_type="adjusted_close",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 24, 20, tzinfo=UTC),
            effective_date=date(2026, 7, 24),
            value=110,
            content="The completed 2026-07-24 adjusted close.",
        )
    )

    def collect(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = {
            "fundamentals": CollectionDomainResult(
                domain="fundamentals",
                state="partial",
                sources=_sources(
                    "fixture.snapshot",
                    datetime(2026, 7, 29, 15, tzinfo=UTC),
                ),
                temporal_bases=("near_live_advisory",),
                evidence_refs=(partial.evidence.ref,),
                diagnostic=CollectionDiagnostic(code="bounded_snapshot"),
            ),
            "market": CollectionDomainResult(
                domain="market",
                state="partial",
                sources=_sources(
                    "fixture.market",
                    datetime(2026, 7, 29, 15, tzinfo=UTC),
                ),
                temporal_bases=("pit",),
                evidence_refs=(market_evidence.evidence.ref,),
                diagnostic=CollectionDiagnostic(code="provider_failure"),
            ),
            "news": CollectionDomainResult(
                domain="news",
                state="data",
                sources=_sources(
                    "fallback.news",
                    datetime(2026, 7, 29, 15, tzinfo=UTC),
                    fallback=True,
                ),
                temporal_bases=("pit",),
                evidence_refs=(fallback.evidence.ref,),
            ),
            "social": CollectionDomainResult(
                domain="social",
                state="data",
                sources=_sources(
                    "fixture.social",
                    datetime(2026, 7, 30, 15, tzinfo=UTC),
                ),
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
            evidence=(partial, market_evidence, fallback, stale),
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
            stock_series_evidence_ref=market_evidence.evidence.ref,
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

    node = next(item for item in repository.get_timeline("NVDA").nodes if item.id == result.run_id)
    domains = {item.domain: item for item in node.collection_summary.domains}
    assert domains["fundamentals"].state.value == "partial"
    assert domains["market"].diagnostic.code == "provider_failure"
    assert domains["news"].sources[0].fallback is True
    assert domains["social"].state.value == "empty"
    assert domains["social"].diagnostic.code == "outside_temporal_boundary"
    assert {item.ref for item in result.evidence.items} == {
        partial.evidence.ref,
        market_evidence.evidence.ref,
        fallback.evidence.ref,
    }
    assert {item.domain: item.status.value for item in node.research_availability.domains} == {
        "fundamentals": "limited",
        "market": "limited",
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
            sources=_sources("fixture.news", request.window_end),
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

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)
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
            sources=_sources("fixture.news", request.window_end),
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
                "decision": synthesis.decision.model_copy(update={"evidence_refs": (sibling_ref,)})
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
    assert sibling_ref not in {item.ref for item in synthesis_inputs[0].incremental_evidence.items}
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

    def collect_copied(request: IncrementalCollectionRequest) -> IncrementalCollectionResult:
        domains = list(_unavailable_domains(request))
        domain = request.enabled_domains.index("news")
        domains[domain] = CollectionDomainResult(
            domain="news",
            state="data",
            sources=_sources(
                copied.source,
                datetime(2026, 7, 24, 18, tzinfo=UTC),
            ),
            temporal_bases=("near_live_advisory",),
            evidence_refs=(copied.ref,),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(IncrementalEvidenceCandidate(evidence=copied),),
        )

    with pytest.raises(EvidenceConflictError, match="must not copy Full Baseline"):
        _incremental_service(
            app_settings,
            repository,
            collector=collect_copied,
            now=lambda: datetime(2026, 7, 24, 19, tzinfo=UTC),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


def test_incremental_commit_rejects_collection_refs_outside_current_bundle(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_ref = repository.get_evidence(baseline.run_id).items[0].ref
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A current filing for the Incremental bundle.",
        )
    )
    original_complete = repository.complete_incremental

    def complete_with_stale_summary(run_id, result, *, evidence, products):
        domains = tuple(
            domain.model_copy(update={"evidence_refs": (baseline_ref,)})
            if domain.domain == "news"
            else domain
            for domain in products.collection_summary.domains
        )
        invalid_products = products.model_copy(
            update={
                "collection_summary": products.collection_summary.model_copy(
                    update={"domains": domains}
                )
            }
        )
        return original_complete(
            run_id,
            result,
            evidence=evidence,
            products=invalid_products,
        )

    monkeypatch.setattr(repository, "complete_incremental", complete_with_stale_summary)

    with pytest.raises(
        EvidenceConflictError,
        match="Collection Summary references evidence outside the current Incremental bundle",
    ):
        _incremental_service(
            app_settings,
            repository,
            collector=lambda request: _pit_collection(request, candidate),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").nodes) == (baseline.run_id,)


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
                        code="attribution.unreliable",
                        message="The bounded update cannot resolve attribution.",
                        origin="semantic",
                        evidence_refs=("ev_000000000000",) if warning_has_dangling_ref else (),
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


@pytest.mark.parametrize(
    "code",
    (
        "required_coverage",
        "required_coverage.social",
        "availability.social_missing",
        "coverage.required.social",
    ),
)
def test_missing_optional_availability_has_no_full_research_reason_code(code) -> None:
    with pytest.raises(ValidationError, match="FullResearchRequiredReason"):
        FullResearchRequiredReason(
            code=code,
            message="Optional social coverage is missing.",
            origin="deterministic",
        )
