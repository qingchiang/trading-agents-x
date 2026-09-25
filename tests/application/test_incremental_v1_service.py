from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from tests.research_helpers import default_incremental_synthesizer
from tests.support.factories import analyst_report, research_decision
from tests.support.incremental import (
    _incremental_service,
    _pit_collection,
    _sources,
    _unavailable_domains,
)
from tests.support.service import _equity_resolver, _Graph, _service
from tradingagents.application.service import AnalysisService
from tradingagents.domain.collection import (
    CollectionDiagnostic,
    CollectionDomainResult,
    CollectionSummary,
    IncrementalCollectionRequest,
    IncrementalEvidenceCandidate,
)
from tradingagents.domain.common import ReportLanguage, RunStatus
from tradingagents.domain.data import SourceObservation
from tradingagents.domain.decision_components import baseline_component_ids
from tradingagents.domain.errors import (
    InvalidIncrementalBaselineError,
    NoInformationAdvancementError,
    UnsupportedInstrumentError,
)
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem, EvidenceOrigin
from tradingagents.domain.incremental import (
    FullResearchRequiredReason,
    IncrementalCollectionResult,
    IncrementalDecisionOutcome,
    ReassessmentDisposition,
)
from tradingagents.domain.performance import (
    BenchmarkSeriesResult,
    MarketSeriesPoint,
    MarketSeriesResult,
)
from tradingagents.domain.runs import AnalysisRequest
from tradingagents.llm.runtime import RunLLMs
from tradingagents.persistence._repository_common import EvidenceConflictError
from tradingagents.persistence.models import (
    DecisionRecord,
    ResearchNodeRecord,
    RunEvidenceRecord,
    RunRecord,
)
from tradingagents.research.full.state import GraphExecution
from tradingagents.research.incremental.synthesis import (
    _incremental_brief_fallback_title,
)
from tradingagents.research.synthesis.structured_output import StructuredOutputError


@pytest.mark.parametrize(
    ("language", "expected"),
    (
        (ReportLanguage.ENGLISH, "Incremental analysis"),
        (ReportLanguage.SIMPLIFIED_CHINESE, "增量分析"),
        (ReportLanguage.JAPANESE, "増分分析"),
    ),
)
def test_incremental_brief_fallback_title_is_localized(
    language: ReportLanguage,
    expected: str,
) -> None:
    assert _incremental_brief_fallback_title(language) == expected


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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


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
        collector=lambda request, *, data_context: _fundamentals_collection(request, candidate),
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
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


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
        collector=lambda request, *, data_context: _fundamentals_collection(request, candidate),
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
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
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
    repository.set_instrument_name(baseline.run_id, "NVIDIA Corporation")
    repository.set_instrument_local_name(baseline.run_id, "英伟达")
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    node = repository.get_research_node(result.run_id)
    assert node is not None
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
    assert node.decision_outcome is IncrementalDecisionOutcome.UNCHANGED
    assert node.decision_outcome_reason
    assert repository.get_incremental_context(result.run_id).analysis_brief is not None
    assert repository.get_incremental_context(result.run_id).full_baseline.run_id == baseline.run_id
    assert node.decision is not None
    assert len(synthesis_inputs) == 1
    assert not hasattr(synthesis_inputs[0], "outcome_review_status")
    assert repository.get_run(result.run_id).method_snapshot["prompt_versions"] == {
        "incremental_synthesis": "v4-research-references",
    }
    assert result.metrics.llm_calls == 0
    assert result.instrument_name == "NVIDIA Corporation"
    assert result.instrument_local_name == "英伟达"
    assert repository.get_run(result.run_id).instrument_name == "NVIDIA Corporation"
    assert repository.get_run(result.run_id).instrument_local_name == "英伟达"
    with repository.sessions.begin() as session:
        historical_node = session.get(ResearchNodeRecord, result.run_id)
        products = dict(historical_node.incremental_products_json)
        products.pop("analysis_brief")
        historical_node.incremental_products_json = products
    assert repository.get_incremental_context(result.run_id).analysis_brief is None
    with repository.sessions.begin() as session:
        historical = session.get(RunRecord, result.run_id)
        historical.instrument_name = None
        historical.instrument_local_name = None
    assert repository.get_run(result.run_id).instrument_name == "NVIDIA Corporation"
    assert repository.get_run(result.run_id).instrument_local_name == "英伟达"


@pytest.mark.parametrize(
    ("outcome", "decision_change", "disposition", "message"),
    (
        ("unchanged", True, "reaffirmed", "unchanged outcome requires the baseline Decision"),
        ("updated", False, "reaffirmed", "updated outcome requires a changed Decision"),
        ("unchanged", False, "overturned", "overturned reassessment requires updated outcome"),
    ),
)
def test_incremental_service_rejects_inconsistent_decision_outcomes_atomically(
    app_settings,
    repository,
    outcome: str,
    decision_change: bool,
    disposition: str,
    message: str,
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
            content="A bounded update for outcome validation.",
        )
    )

    def synthesize(input_):
        synthesis = default_incremental_synthesizer(input_)
        entries = tuple(
            entry.model_copy(update={"disposition": ReassessmentDisposition(disposition)})
            for entry in synthesis.reassessment.entries
        )
        decision = synthesis.decision
        if decision_change:
            decision = decision.model_copy(update={"thesis": "A materially updated thesis."})
        return synthesis.model_copy(
            update={
                "reassessment": synthesis.reassessment.model_copy(update={"entries": entries}),
                "decision_outcome": IncrementalDecisionOutcome(outcome),
                "decision_outcome_reason": "The bounded evidence supports this outcome.",
                "decision": decision,
            }
        )

    with pytest.raises(ValueError, match=message):
        _incremental_service(
            app_settings,
            repository,
            collector=lambda request, *, data_context: _pit_collection(request, candidate),
            synthesizer=synthesize,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    failed_run = repository.list_runs(status=RunStatus.FAILED).items[0]
    assert repository.evidence_status(failed_run.id).status == "pending"
    assert repository.get_research_node(failed_run.id) is None
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


def test_incremental_outcome_is_optional_only_when_reading_historical_products(
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
            content="A historical compatibility fixture.",
        )
    )
    result = _incremental_service(
        app_settings,
        repository,
        collector=lambda request, *, data_context: _pit_collection(request, candidate),
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    with repository.sessions.begin() as session:
        record = session.get(ResearchNodeRecord, result.run_id)
        assert record is not None
        historical = dict(record.incremental_products_json)
        historical.pop("decision_outcome")
        historical.pop("decision_outcome_reason")
        record.incremental_products_json = historical

    node = repository.get_research_node(result.run_id)
    assert node is not None
    assert node.decision_outcome is None
    assert node.decision_outcome_reason is None


def test_incremental_name_resolution_failure_does_not_block_research(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, baseline.run_id)
        record.instrument_name = None
        record.instrument_local_name = None

    def fail_identity(_ticker, _date):
        raise RuntimeError("identity unavailable")

    def fail_local_name(_ticker, _date, _config):
        raise RuntimeError("local name unavailable")

    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A newly published filing.",
        )
    )

    result = _incremental_service(
        app_settings,
        repository,
        collector=lambda request, *, data_context: _pit_collection(request, candidate),
        identity_resolver=fail_identity,
        local_name_resolver=fail_local_name,
    ).run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date=date(2026, 7, 24),
            research_kind="incremental",
            full_baseline_run_id=baseline.run_id,
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.instrument_name is None
    assert result.instrument_local_name is None


@pytest.mark.parametrize(
    ("monolithic_failure", "section_failure"),
    (
        ("truncated", False),
        ("schema_validation", False),
        ("schema_validation", True),
        ("repair_schema_validation", False),
    ),
)
def test_incremental_assessment_respects_one_repair_budget(
    app_settings,
    repository,
    monolithic_failure: str,
    section_failure: bool,
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
        def __init__(self):
            self.calls = 0

        def invoke(self, _prompt, config=None):
            del config
            self.calls += 1
            return AIMessage(
                content="The bounded update reaffirms the baseline.",
                response_metadata={"finish_reason": "length"},
            )

    class _SerializerLLM:
        preferred_structured_output_method = "function_calling"
        structured_output_max_tokens = 16_384

        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []

        def with_structured_output(
            self,
            schema,
            *,
            method=None,
            include_raw=False,
            **_kwargs,
        ):
            assert include_raw is True
            self.calls.append((schema.__name__, method))
            if schema.__name__ == "_IncrementalAssessmentPayload":
                if method == "json_mode":
                    if monolithic_failure == "repair_schema_validation":
                        return _Invoker(
                            {
                                "raw": AIMessage(content="{}"),
                                "parsed": {"reassessment": {"entries": []}},
                                "parsing_error": None,
                            }
                        )
                    parsed = (
                        {"reassessment": {"entries": []}}
                        if section_failure
                        else {
                            "reassessment": {
                                "entries": [
                                    {
                                        "component_id": component_id,
                                        "disposition": "reaffirmed",
                                        "reason": (
                                            "The bounded update does not change this component."
                                        ),
                                    }
                                    for component_id in component_ids
                                ]
                            },
                            "decision_outcome": "unchanged",
                            "decision_outcome_reason": (
                                "No complete Decision field needs to change."
                            ),
                            "full_research_required_reasons": [],
                        }
                    )
                    return _Invoker(
                        {
                            "raw": AIMessage(content=""),
                            "parsed": parsed,
                            "parsing_error": None,
                        }
                    )
                if monolithic_failure == "repair_schema_validation":
                    return _Invoker(RuntimeError("primary unavailable"))
                if monolithic_failure == "schema_validation":
                    return _Invoker(
                        {
                            "raw": AIMessage(content="{}"),
                            "parsed": {"reassessment": {"entries": []}},
                            "parsing_error": None,
                        }
                    )
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
            raise AssertionError(f"unexpected schema: {schema.__name__}")

    semantic = _SemanticLLM()
    serializer = _SerializerLLM()
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: RunLLMs(
            quick=semantic,
            deep=semantic,
            quick_serializer=serializer,
            deep_serializer=serializer,
        ),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda request, *, data_context: _pit_collection(request, candidate),
        incremental_synthesizer=None,
        now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
    )

    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )

    if section_failure:
        with pytest.raises(StructuredOutputError):
            service.run(request)
        assert semantic.calls == 1
        assert serializer.calls == [
            ("_IncrementalAssessmentPayload", None),
            ("_IncrementalAssessmentPayload", "json_mode"),
        ]
        failed = repository.list_runs(status=RunStatus.FAILED).items
        assert len(failed) == 1
        failed_run_id = failed[0].id
        assert repository.evidence_status(failed_run_id).status == "pending"
        assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
            baseline.run_id,
        )
        with repository.sessions() as session:
            assert session.get(ResearchNodeRecord, failed_run_id) is None
            assert session.get(RunEvidenceRecord, failed_run_id) is None
            assert (
                session.query(DecisionRecord).filter(DecisionRecord.run_id == failed_run_id).count()
                == 0
            )
        return

    if monolithic_failure == "repair_schema_validation":
        with pytest.raises(StructuredOutputError):
            service.run(request)
        assert semantic.calls == 1
        assert serializer.calls == [
            ("_IncrementalAssessmentPayload", None),
            ("_IncrementalAssessmentPayload", "json_mode"),
        ]
        failed = repository.list_runs(status=RunStatus.FAILED).items
        assert len(failed) == 1
        return

    result = service.run(request)

    assert semantic.calls == 1
    assert serializer.calls == [
        ("_IncrementalAssessmentPayload", None),
        ("_IncrementalAssessmentPayload", "json_mode"),
    ]
    assert result.status is RunStatus.SUCCEEDED
    node = repository.get_timeline("NVDA").all_nodes[-1]
    assert len(node.reassessment.entries) == len(component_ids)
    assert any(
        event.event_type == "node.output_recovered"
        and event.payload["method"] == "json_mode_recovered"
        for event in repository.list_events(result.run_id)
    )


@pytest.mark.parametrize(
    ("outcome", "full_research_required", "core_recovery"),
    (
        ("unchanged", False, False),
        ("unchanged", True, False),
        ("updated", False, False),
        ("updated", False, True),
        ("updated", True, False),
    ),
)
@pytest.mark.parametrize("repeat_retrieval", [False, True])
@pytest.mark.parametrize("extra_source_text", ["", "\nAdditional source limitation."])
def test_production_incremental_synthesis_generates_decision_only_when_updated(
    app_settings,
    repository,
    outcome: str,
    full_research_required: bool,
    core_recovery: bool,
    extra_source_text: str,
    repeat_retrieval: bool,
) -> None:
    from dataclasses import replace

    from tradingagents.domain.decision import MarketReferenceLevel
    from tradingagents.domain.evidence import EvidenceTable, EvidenceTableColumn, EvidenceTableRow

    class ReferenceGraph(_Graph):
        def execute(self, context, **kwargs):
            execution = super().execute(context, **kwargs)
            ref = execution.evidence.items[0].ref
            reference = MarketReferenceLevel(label="Baseline conditional value", value=100,
                basis="derived", evidence_refs=(ref,), date_evidence_refs=(ref,),
                as_of_date=date(2026, 7, 20), unit="USD", interpretation="Baseline assumptions.")
            table = EvidenceTable.create(
                title="LOCAL-BASELINE-VALIDATION-ONLY", purpose="Historical price",
                columns=(EvidenceTableColumn(key="close", label="Close"),),
                rows=(EvidenceTableRow(id="quote", cells={"close": {"raw_value": 100}}),),
                evidence_refs=(ref,), source_format="structured",
            )
            evidence = EvidenceBundle(
                instrument=execution.evidence.instrument,
                analysis_date=execution.evidence.analysis_date,
                items=execution.evidence.items, tables=(table,),
                sealed_at=execution.evidence.sealed_at,
            )
            return replace(execution, evidence=evidence, decision=execution.decision.model_copy(
                update={"market_reference_levels": (reference,)}))

    baseline = _service(app_settings, repository, graph_factory=ReferenceGraph).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_decision = repository.get_result(baseline.run_id).decision
    assert baseline_decision is not None
    observation = SourceObservation(
        source="fixture.news", kind="filing", key="quarterly-update",
        values={"detail": "UNIQUE-SOURCE-FACT", "amount": 0, "unit": "USD"},
        retrieved_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
        available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
        timing="Publication time verified", fallback=True,
    )
    evidence = observation.evidence(date(2026, 7, 24), instrument="NVDA")
    if extra_source_text:
        evidence = EvidenceItem.create(
            **evidence.model_dump(exclude={"ref", "content", "origins"}),
            origins=evidence.origins,
            content=observation.content + extra_source_text,
        )
    candidate = IncrementalEvidenceCandidate(evidence=evidence)
    candidates = (candidate,)
    if repeat_retrieval:
        from dataclasses import replace

        later_observation = replace(observation, retrieved_at=datetime(2026, 7, 24, 13, tzinfo=UTC))
        later = later_observation.evidence(date(2026, 7, 24), instrument="NVDA")
        if extra_source_text:
            later = EvidenceItem.create(
                **later.model_dump(exclude={"ref", "content", "origins"}), origins=later.origins,
                content=later_observation.content + extra_source_text,
            )
        candidates += (IncrementalEvidenceCandidate(evidence=later),)
    component_ids = baseline_component_ids(baseline_decision)

    def collect(request, *, data_context):
        result = _pit_collection(request, candidate)
        domains = tuple(
            domain.model_copy(update={"sources": _sources(
                candidate.evidence.source, datetime(2026, 7, 24, 13 if repeat_retrieval else 12, tzinfo=UTC), fallback=True,
            ), "evidence_refs": tuple(c.evidence.ref for c in candidates)}) if domain.domain == "news" else domain
            for domain in result.collection_summary.domains
        )
        return result.model_copy(update={
            "evidence": candidates,
            "collection_summary": result.collection_summary.model_copy(
                update={"domains": domains},
            ),
        })

    class _Invoker:
        def __init__(self, parsed, prompts):
            self.parsed = parsed
            self.prompts = prompts

        def invoke(self, prompt, config=None):
            del config
            self.prompts.append(prompt)
            if isinstance(self.parsed, dict) and "raw" in self.parsed:
                return self.parsed
            return {
                "raw": AIMessage(content=""),
                "parsed": self.parsed,
                "parsing_error": None,
            }

    class _SemanticLLM:
        def __init__(self):
            self.prompts: list[str] = []

        def invoke(self, prompt, config=None):
            del config
            self.prompts.append(prompt)
            return AIMessage(content="The bounded update was assessed.")

    class _SerializerLLM:
        preferred_structured_output_method = "function_calling"
        structured_output_max_tokens = 16_384

        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []
            self.prompts: list[str] = []

        def with_structured_output(
            self,
            schema,
            *,
            method=None,
            include_raw=False,
            **_kwargs,
        ):
            assert include_raw is True
            self.calls.append((schema.__name__, method))
            if schema.__name__ == "_IncrementalAssessmentPayload":
                return _Invoker(
                    {
                        "reassessment": {
                            "entries": [
                                {
                                    "component_id": component_id,
                                    "disposition": (
                                        "strengthened" if component_id == "thesis" else "reaffirmed"
                                    ),
                                    "reason": "The new evidence was assessed in bounds.",
                                }
                                for component_id in component_ids
                            ]
                        },
                        "decision_outcome": outcome,
                        "decision_outcome_reason": (
                            "No Decision field changes."
                            if outcome == "unchanged"
                            else "The thesis must be rewritten."
                        ),
                        "full_research_required_reasons": (
                            [
                                {
                                    "code": "attribution.unreliable",
                                    "message": "Attribution remains unresolved.",
                                    "origin": "semantic",
                                    "evidence_refs": [],
                                }
                            ]
                            if full_research_required
                            else []
                        ),
                    },
                    self.prompts,
                )
            if schema.__name__ == "_IncrementalDecisionPayload" and core_recovery:
                return _Invoker({"raw": AIMessage(content="", response_metadata={"finish_reason": "length"}), "parsed": None}, self.prompts)
            if schema.__name__ in {"_IncrementalDecisionPayload", "_IncrementalDecisionSection"}:
                updated = baseline_decision.model_dump(mode="json")
                updated['thesis'] = "The new filing materially updates the thesis."
                if schema.__name__ == "_IncrementalDecisionSection":
                    updated.pop('market_reference_levels')
                    for scenario in updated['scenarios']:
                        scenario.pop('reference_ranges')
                else:
                    updated['market_reference_levels'][0]['value'] = 140
                    observed = {**updated['market_reference_levels'][0], "basis": "observed",
                                "value": 100, "source_locator": {
                                    "evidence_ref": baseline_decision.evidence_refs[0],
                                    "table_id": repository.get_result(baseline.run_id).evidence.tables[0].id,
                                    "row_id": "quote", "column": "close",
                                }}
                    updated['market_reference_levels'].extend([
                        observed,
                        {**observed, "source_locator": {**observed["source_locator"],
                                                       "table_id": "et_000000000000"}},
                        {"label": "Invalid optional reference"},
                    ])
                return _Invoker({"decision": updated}, self.prompts)
            raise AssertionError(f"unexpected schema: {schema.__name__}")

    serializer = _SerializerLLM()
    semantic = _SemanticLLM()
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: RunLLMs(
            quick=semantic,
            deep=semantic,
            quick_serializer=serializer,
            deep_serializer=serializer,
        ),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=collect,
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

    expected_calls = [("_IncrementalAssessmentPayload", None)]
    if outcome == "updated":
        expected_calls.append(("_IncrementalDecisionPayload", None))
    if core_recovery:
        expected_calls.append(("_IncrementalDecisionSection", None))
    assert serializer.calls == expected_calls
    confidence_instruction = (
        "Never express final Decision confidence as a number, decimal, percentage, or probability"
    )
    assert semantic.prompts
    assert serializer.prompts
    saved = repository.get_evidence(result.run_id)
    assert {i.ref for i in saved.items} == {c.evidence.ref for c in candidates}
    assert all(c.evidence in saved.items for c in candidates)
    for prompt in (*semantic.prompts, *serializer.prompts):
        assert "LOCAL-BASELINE-VALIDATION-ONLY" not in prompt
        assert prompt.count("UNIQUE-SOURCE-FACT") == (2 if extra_source_text else 1)
        if extra_source_text:
            assert "Additional source limitation." in prompt
        assert all(c.evidence.ref in prompt for c in candidates)
        if repeat_retrieval:
            assert "2026-07-24T13:00:00+00:00" in prompt
            payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('{"full_baseline_run_id":'):])
            projected = payload["incremental_evidence"]
            expanded = {}
            for item in projected["items"]:
                item = deepcopy(item)
                canonical = item.pop("same_observation_as", None)
                if canonical:
                    item = {**deepcopy(expanded[canonical]), **item}
                if item.pop("content_from_observation", False):
                    observed = item["provenance"]["observation"]
                    item["content"] = f"{observed['kind']}: {observed['key']}\n" + json.dumps(
                        observed["values"], ensure_ascii=False, sort_keys=True,
                    )
                expanded[item["ref"]] = item
            for group in projected["observation_groups"]:
                for retrieval in group["retrievals"]:
                    for field in retrieval["fields"]:
                        parent = expanded[retrieval["ref"]]
                        for part in field["path"][:-1]:
                            parent = parent[part]
                        parent[field["path"][-1]] = field["value"]
            assert expanded == {item.ref: item.model_dump(mode="json") for item in saved.items}
        assert "2026-07-22T12:00:00Z" in prompt
        assert "2026-07-24T12:00:00+00:00" in prompt
        assert "Publication time verified" in prompt
        assert '"fallback":true' in prompt
    assert all(
        confidence_instruction in prompt for prompt in (*semantic.prompts, *serializer.prompts)
    )
    assert result.decision is not None
    assert (
        result.decision.model_dump(mode="json") == baseline_decision.model_dump(mode="json")
    ) is (outcome == "unchanged")
    assert baseline_decision.market_reference_levels[0].value == 100
    if outcome == "unchanged":
        assert result.decision.market_reference_levels[0].value == 100
    elif core_recovery:
        assert result.decision.market_reference_levels == ()
    else:
        assert [level.value for level in result.decision.market_reference_levels] == [140, 100]
        assert any(
            event.event_type == "decision.reference_omitted"
            and event.payload["validation_issues"] == ["reference.locator_invalid"]
            for event in repository.list_events(result.run_id)
        )
    if outcome == "updated":
        assert any(event.event_type == 'decision.reference_omitted'
                   for event in repository.list_events(result.run_id))
    node = repository.get_research_node(result.run_id)
    assert node is not None
    assert node.decision_outcome is IncrementalDecisionOutcome(outcome)
    assert bool(node.full_research_required_reasons) is full_research_required


def test_incremental_assessment_repair_consumes_the_shared_decision_repair_budget(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    baseline_decision = repository.get_result(baseline.run_id).decision
    assert baseline_decision is not None
    component_ids = baseline_component_ids(baseline_decision)
    candidate = IncrementalEvidenceCandidate(
        evidence=EvidenceItem.create(
            source="fixture.news",
            evidence_type="filing",
            requested_date=date(2026, 7, 24),
            available_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
            content="A bounded update that exercises the shared repair budget.",
        )
    )

    class _Invoker:
        def __init__(self, parsed):
            self.parsed = parsed

        def invoke(self, _prompt, config=None):
            del config
            return {
                "raw": AIMessage(content=""),
                "parsed": self.parsed,
                "parsing_error": None,
            }

    class _SemanticLLM:
        def invoke(self, _prompt, config=None):
            del config
            return AIMessage(content="The bounded update was assessed.")

    class _SerializerLLM:
        preferred_structured_output_method = "function_calling"
        structured_output_max_tokens = 16_384

        def __init__(self):
            self.calls: list[tuple[str, str | None]] = []

        def with_structured_output(
            self,
            schema,
            *,
            method=None,
            include_raw=False,
            **_kwargs,
        ):
            assert include_raw is True
            self.calls.append((schema.__name__, method))
            if schema.__name__ == "_IncrementalAssessmentPayload":
                if method is None:
                    return _Invoker({"reassessment": {"entries": []}})
                return _Invoker(
                    {
                        "reassessment": {
                            "entries": [
                                {
                                    "component_id": component_id,
                                    "disposition": "reaffirmed",
                                    "reason": "The component was reassessed.",
                                }
                                for component_id in component_ids
                            ]
                        },
                        "decision_outcome": "updated",
                        "decision_outcome_reason": "The thesis must be rewritten.",
                        "full_research_required_reasons": [],
                    }
                )
            if schema.__name__ == "_IncrementalDecisionPayload":
                return _Invoker({"decision": baseline_decision.model_dump(mode="json")})
            raise AssertionError(f"unexpected schema: {schema.__name__}")

    serializer = _SerializerLLM()
    semantic = _SemanticLLM()
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: RunLLMs(
            quick=semantic,
            deep=semantic,
            quick_serializer=serializer,
            deep_serializer=serializer,
        ),
        graph_factory=_Graph,
        identity_resolver=lambda symbol, _date: {"company_name": symbol},
        eligibility_resolver=_equity_resolver,
        local_name_resolver=lambda _ticker, _date, _config: None,
        incremental_collector=lambda request, *, data_context: _pit_collection(request, candidate),
        incremental_synthesizer=None,
        now=lambda: datetime(2026, 7, 24, 20, tzinfo=UTC),
    )

    with pytest.raises(StructuredOutputError):
        service.run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert serializer.calls == [
        ("_IncrementalAssessmentPayload", None),
        ("_IncrementalAssessmentPayload", "json_mode"),
        ("_IncrementalDecisionPayload", None),
    ]
    failed_run = repository.list_runs(status=RunStatus.FAILED).items[0]
    assert repository.evidence_status(failed_run.id).status == "pending"
    assert repository.get_research_node(failed_run.id) is None


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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
        observed.append((dict(data_context.config), request))
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

    def eligibility(symbol: str, *, data_context):
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
            collector=lambda request, *, data_context: _pit_collection(request, candidate),
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
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


def test_incremental_retry_uses_the_retained_run_dataflow_configuration(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    observed_news_routes = []

    def eligibility(symbol: str, *, data_context):
        observed_news_routes.append(data_context.config["data_vendors"]["news_data"])
        return {"symbol": symbol, "quote_type": "EQUITY"}

    service = _incremental_service(
        app_settings,
        repository,
        collector=lambda request, *, data_context: IncrementalCollectionResult(
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
            collector=lambda request, *, data_context: _pit_collection(request, candidate),
            synthesizer=synthesize,
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


def test_incremental_service_rejects_no_information_advancement_before_synthesis(
    app_settings,
    repository,
) -> None:
    baseline = _service(app_settings, repository).run(
        AnalysisRequest(ticker="NVDA", analysis_date=date(2026, 7, 20))
    )
    synthesized = []

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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
    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )
    assert any(
        event.event_type == "incremental.no_advancement"
        for event in repository.list_events(failed[0].id)
    )


@pytest.mark.parametrize("later_market_snapshot,series_minute", [(False, 0), (True, 0), (True, 1)])
def test_completed_stock_session_advances_and_persists_one_sealed_calculation(
    app_settings,
    repository,
    later_market_snapshot,
    series_minute,
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
            origins=(
                EvidenceOrigin(
                    source="fixture.market",
                    evidence_type="adjusted_close",
                    retrieved_at="2026-07-24T21:00:00Z",
                    fallback=True,
                    temporal_scope="point_in_time",
                ),
            )
            if later_market_snapshot
            else (),
        ),
        available_on=date(2026, 7, 24),
    )

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
        extra = IncrementalEvidenceCandidate(
            evidence=EvidenceItem.create(
                source="fixture.market",
                evidence_type="market_snapshot",
                requested_date=date(2026, 7, 24),
                available_at=datetime(2026, 7, 24, 20, tzinfo=UTC),
                content="A separate snapshot from the same provider, retrieved one minute later.",
                fallback=True,
                origins=(
                    EvidenceOrigin(
                        source="fixture.market",
                        evidence_type="market_snapshot",
                        retrieved_at="2026-07-24T21:01:00Z",
                        fallback=True,
                        temporal_scope="point_in_time",
                    ),
                ),
            )
        )
        domains = list(_unavailable_domains(request))
        market = request.enabled_domains.index("market")
        domains[market] = CollectionDomainResult(
            domain="market",
            state="data",
            sources=_sources(
                "fixture.market",
                datetime(2026, 7, 24, 21, int(later_market_snapshot), tzinfo=UTC),
                fallback=True,
            ),
            temporal_bases=("pit",),
            evidence_refs=(market_evidence.evidence.ref,)
            + ((extra.evidence.ref,) if later_market_snapshot else ()),
        )
        return IncrementalCollectionResult(
            collection_summary=CollectionSummary(
                version=request.version,
                market=request.market,
                domains=tuple(domains),
            ),
            evidence=(market_evidence,) + ((extra,) if later_market_snapshot else ()),
            stock_series=MarketSeriesResult(
                instrument=request.instrument,
                source="fixture.market",
                fallback=True,
                adjustment_basis="adjusted_close",
                retrieved_at=datetime(2026, 7, 24, 21, series_minute, tzinfo=UTC),
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

    service = _incremental_service(
        app_settings,
        repository,
        collector=collect,
        now=lambda: datetime(2026, 7, 25, 5, tzinfo=UTC),
    )
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        research_kind="incremental",
        full_baseline_run_id=baseline.run_id,
    )
    if series_minute:
        with pytest.raises(ValueError, match="requires admitted current market Evidence"):
            service.run(request)
        return
    result = service.run(request)
    events = [event.event_type for event in repository.list_events(result.run_id)]
    assert events.index("run.started") < events.index("incremental.collection_started")
    assert events.index("incremental.collection_started") < events.index(
        "incremental.collection_completed"
    )
    assert events.index("incremental.synthesis_completed") < events.index("run.commit_started")
    assert events.index("run.commit_started") < events.index("run.succeeded")

    node = next(
        item for item in repository.get_timeline("NVDA").all_nodes if item.id == result.run_id
    )
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    node = next(
        item for item in repository.get_timeline("NVDA").all_nodes if item.id == result.run_id
    )
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    node = next(
        item for item in repository.get_timeline("NVDA").all_nodes if item.id == result.run_id
    )
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    node = next(
        item for item in repository.get_timeline("NVDA").all_nodes if item.id == result.run_id
    )
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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
                "decision_outcome": IncrementalDecisionOutcome.UPDATED,
                "decision_outcome_reason": "The Decision references newly admitted Evidence.",
                "decision": synthesis.decision.model_copy(update={"evidence_refs": (sibling_ref,)}),
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
    assert {node.id for node in repository.get_timeline("NVDA").all_nodes} == {
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

    def collect_copied(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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

    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
        baseline.run_id,
    )


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
            collector=lambda request, *, data_context: _pit_collection(request, candidate),
        ).run(
            AnalysisRequest(
                ticker="NVDA",
                analysis_date=date(2026, 7, 24),
                research_kind="incremental",
                full_baseline_run_id=baseline.run_id,
            )
        )

    assert tuple(node.id for node in repository.get_timeline("NVDA").all_nodes) == (
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

    def collect(
        request: IncrementalCollectionRequest, *, data_context
    ) -> IncrementalCollectionResult:
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
        collector=lambda request, *, data_context: _pit_collection(request, candidate),
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
            item for item in repository.get_timeline("NVDA").all_nodes if item.id == result.run_id
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
