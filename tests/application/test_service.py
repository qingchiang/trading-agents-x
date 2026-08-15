from __future__ import annotations

import io
import json
import operator
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta, timezone
from threading import Barrier, Lock
from typing import Annotated, TypedDict
from uuid import uuid4

import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from tests.factories import analyst_report, research_decision
from tradingagents.application.anchor_readiness import (
    AnchorReadinessError,
    AnchorReadinessReason,
    AnchorReadinessResult,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    ResearchUpdateTransitionCoverage,
    RunMetrics,
    RunStatus,
)
from tradingagents.application.live_thesis_validation import (
    ReviewedLiveThesisScenario,
    validate_live_thesis,
)
from tradingagents.application.llms import RunLLMs
from tradingagents.application.market_readiness import MarketDataNotReadyError
from tradingagents.application.outcomes import OutcomeObservation, OutcomeSettlement
from tradingagents.application.reflection import OutcomeReflectionDraft
from tradingagents.application.repository import InvalidResearchBaselineError, RunRepository
from tradingagents.application.research import (
    ClaimChange,
    ClaimRevisionDelta,
    CoverageStatus,
    EvidenceSnapshotItem,
    IdentityDisposition,
    IncrementalEscalationReason,
    IncrementalGateResult,
    IndeterminateReason,
    QuestionChange,
    QuestionDispositionAudit,
    QuestionDispositionRecord,
    QuestionRevisionDelta,
    QuestionStatus,
    ResearchChangeConclusion,
    ResearchExecutionStrategy,
    ResearchObjectCoverage,
    ResearchQuestion,
    ResearchRevisionDraft,
    ResearchRevisionRole,
    RevisionDelta,
    SourceObservationInterval,
    SourceRecordSnapshotItem,
    SourceRecordVersion,
    SourceWatermarkSnapshot,
    TransitionCapabilityAttestation,
    TransitionCoverageAttestation,
    assemble_full_revision,
    assess_deterministic_update,
    render_revision_export_markdown,
    validate_experimental_nmc_candidate,
)
from tradingagents.application.runtime import RunCancelled, WorkerShutdown
from tradingagents.application.service import AnalysisService, ChainUpdateExecutionError
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.jp import edinet_common, edinet_news, jp_news, jquants_indicator
from tradingagents.dataflows.jp.calendar import is_tse_open
from tradingagents.graph.research_graph import (
    GraphExecution,
    GraphVisibleRequiredEvidenceError,
)
from tradingagents.provenance import (
    ProvenanceRecord,
    SourceObservation,
    SourceWatermark,
    attach_provenance,
    attach_source_observations,
    attach_source_watermarks,
    extract_source_observations,
    extract_source_watermarks,
)


def _execution(ticker: str) -> GraphExecution:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture evidence",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture evidence.",
    )
    bundle = EvidenceBundle(
        instrument=ticker,
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        evidence_ref=item.ref,
        narrative="Fixture report.",
    )
    decision = research_decision(
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(item.ref,),
    )
    return GraphExecution(
        state={},
        evidence=bundle,
        reports={"market": report},
        decision=decision,
    )


class _Graph:
    barrier: Barrier | None = None
    observed: list[tuple[str, str, str]] = []
    lock = Lock()
    error: Exception | None = None

    def __init__(self, **_kwargs):
        pass

    def execute(self, context, *, on_event, **_kwargs):
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        with self.lock:
            self.observed.append(
                (
                    context.request.ticker,
                    context.settings.llm_provider,
                    get_config()["llm_provider"],
                )
            )
        on_event(
            {
                "event_type": "node.completed",
                "node": "fixture",
                "payload": {"api_key": "must-not-persist"},
            }
        )
        if self.error is not None:
            raise self.error
        return _execution(context.request.ticker)


class _FrontierCapturingGraph(_Graph):
    frontiers: list[datetime] = []

    def execute(self, context, **kwargs):
        assert context.information_frontier is not None
        self.frontiers.append(context.information_frontier)
        execution = _execution(context.request.ticker)
        visible = execution.evidence.items[0].model_copy(
            update={"available_at": context.information_frontier - timedelta(minutes=1)}
        )
        later = EvidenceItem.create(
            source="future fixture",
            evidence_type="late evidence",
            requested_date=context.request.analysis_date,
            effective_date=context.request.analysis_date,
            available_at=context.information_frontier + timedelta(minutes=1),
            content="This was not knowable at the frozen frontier.",
        )
        evidence = EvidenceBundle(
            instrument=context.request.ticker,
            analysis_date=context.request.analysis_date,
            information_frontier=context.information_frontier,
            items=(visible, later),
        )
        return execution.__class__(
            state=execution.state,
            evidence=evidence,
            reports=execution.reports,
            decision=execution.decision,
        )


class _FrontierInspectingGraph(_FrontierCapturingGraph):
    def execute(self, context, **kwargs):
        execution = super().execute(context, **kwargs)
        assert [item.source for item in execution.evidence.items] == ["fixture"]
        return execution


class _MemoryCapturingGraph(_Graph):
    memories = []

    def execute(self, context, **kwargs):
        self.memories.append(context.memory)
        return super().execute(context, **kwargs)


class _SemanticServiceInvoker:
    def __init__(self, schema, metrics):
        self.schema = schema
        self.metrics = metrics

    def invoke(self, prompt, config=None):
        payload = json.loads(prompt.split("BOUNDED INPUT:\n", 1)[1])
        run_id = uuid4()
        metadata = (config or {}).get("metadata", {})
        self.metrics.on_llm_start({}, [prompt], run_id=run_id, metadata=metadata)
        self.metrics.on_llm_end(
            LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                usage_metadata={
                                    "input_tokens": 80,
                                    "output_tokens": 20,
                                    "total_tokens": 100,
                                },
                            )
                        )
                    ]
                ]
            ),
            run_id=run_id,
        )
        return {
            "raw": AIMessage(content=""),
            "parsed": self.schema.model_validate(
                {
                    "language": payload["output_language"],
                    "summary": "既存の主張を再確認しました。",
                    "relationships": [
                        {
                            "evidence_refs": [item["ref"]],
                            "relationship": "support",
                            "suggested_claim_ids": [payload["relevant_claim_ids"][0]],
                        }
                        for item in payload["new_evidence"]
                    ],
                }
            ),
            "parsing_error": None,
        }


class _SemanticServiceLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, metrics):
        self.metrics = metrics

    def with_structured_output(self, schema, **kwargs):
        assert kwargs["include_raw"] is True
        return _SemanticServiceInvoker(schema, self.metrics)


class _ArtifactGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        context.artifact_writer(
            ResearchArtifactDraft(
                node="analyst.market",
                stage="analyst",
                role="market",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=execution.reports["market"],
            )
        )
        return execution


class _EdinetParentCorrectionGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(self, context, **_kwargs):
        execution = _execution(context.request.ticker)
        output = edinet_news.get_news(
            context.request.ticker,
            context.request.analysis_date.isoformat(),
            context.request.analysis_date.isoformat(),
        )
        evidence = execution.evidence.items[0].model_copy(
            update={
                "provenance": {
                    "source_records": [
                        asdict(item) for item in extract_source_observations(output)
                    ],
                    "source_watermarks": [
                        asdict(item) for item in extract_source_watermarks(output)
                    ],
                }
            }
        )
        return execution.__class__(
            state=execution.state,
            evidence=EvidenceBundle(
                instrument=execution.evidence.instrument,
                analysis_date=execution.evidence.analysis_date,
                items=(evidence,),
            ),
            reports=execution.reports,
            decision=execution.decision,
        )


class _MetricFailureGraph:
    def __init__(self, *, metrics, **_kwargs):
        self.metrics = metrics

    def execute(self, _context, **_kwargs):
        run_id = uuid4()
        self.metrics.on_llm_start(
            {},
            ["fixture"],
            run_id=run_id,
            metadata={"research_node": "analyst.market.serialize.core"},
        )
        self.metrics.on_llm_end(
            LLMResult(
                generations=[
                    [
                        ChatGeneration(
                            message=AIMessage(
                                content="fixture",
                                usage_metadata={
                                    "input_tokens": 250,
                                    "output_tokens": 25,
                                    "total_tokens": 275,
                                },
                            )
                        )
                    ]
                ]
            ),
            run_id=run_id,
        )
        raise RuntimeError("fixture structured output failure")


class _MetricCancellationGraph(_MetricFailureGraph):
    def execute(self, *args, **kwargs):
        try:
            return super().execute(*args, **kwargs)
        except RuntimeError as exc:
            raise RunCancelled("cancelled after measured Full work") from exc


class _CheckpointState(TypedDict):
    completed: Annotated[list[str], operator.add]


class _ResumableGraph:
    first_calls = 0
    second_calls = 0
    fail_second_once = True

    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            type(self).first_calls += 1
            execution = _execution(context.request.ticker)
            context.artifact_writer(
                ResearchArtifactDraft(
                    node="fixture.first",
                    stage="analyst",
                    role="market",
                    generation_method=ArtifactGenerationMethod.TOOL_CALL,
                    content=execution.reports["market"],
                )
            )
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.first",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def second_node(_state):
            type(self).second_calls += 1
            if type(self).fail_second_once:
                type(self).fail_second_once = False
                raise RuntimeError("fixture crash after first checkpoint")
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.second",
                    "payload": {},
                }
            )
            return {"completed": ["second"]}

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("second", second_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "second")
        workflow.add_edge("second", END)
        graph = workflow.compile(checkpointer=checkpointer)
        state = graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        assert state["completed"] == ["first", "second"]
        return _execution(context.request.ticker)


class _CancellingCheckpointGraph:
    def __init__(self, **_kwargs):
        pass

    def execute(
        self,
        context,
        *,
        checkpointer,
        checkpoint_thread_id,
        resume,
        on_event,
    ):
        def first_node(_state):
            on_event(
                {
                    "event_type": "node.completed",
                    "node": "fixture.before_cancel",
                    "payload": {},
                }
            )
            return {"completed": ["first"]}

        def cancel_node(_state):
            raise RunCancelled("fixture cooperative cancellation")

        workflow = StateGraph(_CheckpointState)
        workflow.add_node("first", first_node)
        workflow.add_node("cancel", cancel_node)
        workflow.add_edge(START, "first")
        workflow.add_edge("first", "cancel")
        workflow.add_edge("cancel", END)
        graph = workflow.compile(checkpointer=checkpointer)
        graph.invoke(
            None if resume else {"completed": []},
            config={"configurable": {"thread_id": checkpoint_thread_id}},
        )
        raise AssertionError("cancelling graph must not complete")


@pytest.fixture(autouse=True)
def _reset_graph():
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True
    _FrontierCapturingGraph.frontiers = []
    yield
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True
    _FrontierCapturingGraph.frontiers = []


def _eligible_state_assembler(request, execution):
    draft = assemble_full_revision(request, execution)
    market_session = request.analysis_date
    while not is_tse_open(market_session):
        market_session -= timedelta(days=1)
    required_sources = ["EDINET", "TDnet"]
    if "fundamentals" in request.analysts:
        required_sources.append("J-Quants fundamentals")
    if "market" in request.analysts:
        required_sources.append("J-Quants adjusted OHLCV")
    market_record = (
        (
            SourceRecordVersion(
                source="J-Quants adjusted OHLCV",
                record_id=f"market:{request.ticker}",
                version_id=f"market:{request.ticker}:{request.analysis_date}",
                status="published",
                published_at=f"{market_session} 17:00",
                available_at=datetime.combine(
                    market_session,
                    datetime.min.time(),
                    tzinfo=timezone(timedelta(hours=9)),
                ).replace(hour=17),
                title="Completed market observation",
                evidence_ref=execution.evidence.items[0].ref,
                record_kind="market",
                adjustment="split_adjusted",
                unit="JPY",
            ),
        )
        if "market" in request.analysts and request.ticker.endswith(".T")
        else ()
    )
    return draft.model_copy(
        update={
            "coverage": draft.coverage.model_copy(
                update={
                    "claims": tuple(
                        item.model_copy(
                            update={"status": CoverageStatus.COMPLETE, "limitations": ()}
                        )
                        for item in draft.coverage.claims
                    ),
                    "questions": tuple(
                        item.model_copy(
                            update={"status": CoverageStatus.COMPLETE, "limitations": ()}
                        )
                        for item in draft.coverage.questions
                    ),
                    "domains": tuple(
                        item.model_copy(
                            update={"status": CoverageStatus.COMPLETE, "limitations": ()}
                        )
                        for item in draft.coverage.domains
                    ),
                    "limitations": (),
                    "supports_no_material_change": True,
                }
            ),
            "evidence_snapshot": draft.evidence_snapshot.model_copy(
                update={
                    "source_records": (*draft.evidence_snapshot.source_records, *market_record),
                    "source_record_lineage": (
                        *draft.evidence_snapshot.source_record_lineage,
                        *tuple(
                            SourceRecordSnapshotItem(
                                version_id=record.version_id,
                                lineage="new",
                                observed_in_execution=True,
                            )
                            for record in market_record
                        ),
                    ),
                    "source_watermarks": tuple(
                        SourceWatermarkSnapshot(
                            source=source,
                            scanned_start=request.analysis_date,
                            scanned_end=request.analysis_date,
                            status="complete",
                            returned_records=0,
                            reported_records=0,
                        )
                        for source in required_sources
                    )
                    if request.ticker.endswith(".T")
                    else (),
                }
            ),
        }
    )


def _anchor_ready(
    request,
    *,
    information_frontier,
    market_checker,
    news_collector,
    anchor_frontier=None,
):
    del news_collector, anchor_frontier
    try:
        market_checker(request.ticker, request.analysis_date)
    except Exception:
        return AnchorReadinessResult(
            ready=False,
            requested_cutoff=request.analysis_date,
            information_frontier=information_frontier,
            profile_id="jp-listed-equity-v1",
            reasons=(AnchorReadinessReason.MISSING_MARKET_OBSERVATION,),
            metrics=RunMetrics(tool_calls=1),
        )
    return AnchorReadinessResult(
        ready=True,
        requested_cutoff=request.analysis_date,
        information_frontier=information_frontier,
        profile_id="jp-listed-equity-v1",
        metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.25),
    )


def _service(
    app_settings,
    repository: RunRepository,
    graph_factory=_Graph,
    **kwargs,
) -> AnalysisService:
    kwargs.setdefault("state_assembler", _eligible_state_assembler)
    kwargs.setdefault(
        "incremental_gate",
        lambda *_args: IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.COVERAGE_INCOMPLETE
        ),
    )
    kwargs.setdefault("market_data_readiness_checker", lambda *_args: None)
    kwargs.setdefault("anchor_readiness_checker", _anchor_ready)
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=kwargs.pop(
            "llm_factory",
            lambda *_args, **_kwargs: (object(), object()),
        ),
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        **kwargs,
    )


def _experimental_nmc_candidate(
    baseline: ResearchRevisionDraft,
    cutoff: date,
) -> ResearchRevisionDraft:
    inherited_bundle = EvidenceBundle.model_validate(
        {
            **baseline.evidence_snapshot.bundle.model_dump(mode="python"),
            "analysis_date": cutoff,
            "digest": None,
        }
    )
    inherited_snapshot = baseline.evidence_snapshot.model_copy(
        update={
            "bundle": inherited_bundle,
            "lineage": tuple(
                EvidenceSnapshotItem(
                    evidence_ref=item.evidence_ref,
                    lineage="inherited",
                    source_revision_id=baseline.id,
                )
                for item in baseline.evidence_snapshot.lineage
            ),
            "source_record_lineage": tuple(
                SourceRecordSnapshotItem(
                    version_id=item.version_id,
                    lineage="inherited",
                    observed_in_execution=False,
                    source_revision_id=baseline.id,
                )
                for item in baseline.evidence_snapshot.source_record_lineage
            ),
            "source_watermarks": tuple(
                item.model_copy(
                    update={
                        "scanned_end": cutoff,
                        "baseline_cutoff": baseline.cutoff,
                        "overlap_start": baseline.cutoff,
                    }
                )
                for item in baseline.evidence_snapshot.source_watermarks
            ),
        }
    )
    return ResearchRevisionDraft(
        cutoff=cutoff,
        role=ResearchRevisionRole.UPDATE,
        execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
        change_conclusion=ResearchChangeConclusion.NO_MATERIAL_CHANGE,
        delta=RevisionDelta(
            opinion_changed=False,
            claims=tuple(
                ClaimRevisionDelta(
                    object_id=item.id,
                    previous_object_id=item.id,
                    change=ClaimChange.REAFFIRMED,
                    identity_disposition=IdentityDisposition.EXACT_MATCH,
                )
                for item in baseline.current_state.claims
            ),
            questions=tuple(
                QuestionRevisionDelta(
                    object_id=item.id,
                    previous_object_id=item.id,
                    change=QuestionChange.REAFFIRMED,
                    identity_disposition=IdentityDisposition.EXACT_MATCH,
                )
                for item in baseline.current_state.questions
            ),
            inherited_evidence_refs=baseline.current_state.evidence_refs,
        ),
        current_state=baseline.current_state.model_copy(
            update={
                "cutoff": cutoff,
                "scenarios": tuple(
                    item.model_copy(update={"cutoff": cutoff})
                    for item in baseline.current_state.scenarios
                ),
            }
        ),
        coverage=baseline.coverage.model_copy(
            update={
                "claims": tuple(
                    item.model_copy(update={"status": CoverageStatus.COMPLETE})
                    for item in baseline.coverage.claims
                ),
                "questions": tuple(
                    item.model_copy(update={"status": CoverageStatus.COMPLETE})
                    for item in baseline.coverage.questions
                ),
                "domains": tuple(
                    item.model_copy(update={"status": CoverageStatus.COMPLETE})
                    for item in baseline.coverage.domains
                ),
                "supports_no_material_change": True,
            }
        ),
        update_summary=baseline.update_summary.model_copy(
            update={
                "summary": "Bounded assessment reaffirmed the current research state.",
                "baseline_cutoff": baseline.cutoff,
                "analysis_cutoff": cutoff,
                "execution_strategy": ResearchExecutionStrategy.INCREMENTAL,
                "change_conclusion": ResearchChangeConclusion.NO_MATERIAL_CHANGE,
            }
        ),
        evidence_snapshot=inherited_snapshot,
    )


def _transition_coverage(
    baseline: ResearchRevisionDraft,
    cutoff: date,
    *,
    complete: bool,
) -> TransitionCoverageAttestation:
    assert baseline.information_frontier is not None
    required_capabilities = tuple(
        item.capability
        for item in baseline.coverage.anchor_qualification.capabilities
        if item.required
    )
    sources_by_capability = {
        "official_filing": ("EDINET",),
        "timely_disclosure": ("TDnet",),
        "fundamentals": ("J-Quants fundamentals",),
        "market_observation": ("J-Quants adjusted OHLCV",),
        "media": ("Google News",),
        "social_sentiment": ("Google News",),
        "macro": ("Google News",),
    }
    return TransitionCoverageAttestation(
        anchor_frontier=baseline.information_frontier,
        update_frontier=datetime.combine(
            cutoff,
            datetime.max.time(),
            tzinfo=baseline.information_frontier.tzinfo,
        ),
        complete=complete,
        capabilities=tuple(
            TransitionCapabilityAttestation(
                capability=capability,
                complete=complete,
                sources=sources_by_capability[capability.value],
                checked_intervals=(
                    SourceObservationInterval(start=baseline.cutoff, end=cutoff),
                ),
            )
            for capability in required_capabilities
        ),
    )


def test_service_persists_events_before_callback_and_result(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    seen = []

    def callback(event):
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        seen.append(event.event_type)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.evidence is not None
    assert seen == [
        "run.started",
        "node.completed",
        "evidence.sealed",
        "run.succeeded",
    ]
    events = repository.list_events(result.run_id)
    assert events[0].event_type == "run.queued"
    assert events[-1].event_type == "run.succeeded"
    assert events[2].payload["api_key"] == "[REDACTED]"


def test_historical_research_execution_freezes_market_local_end_of_cutoff(
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_FrontierInspectingGraph,
        utc_clock=lambda: datetime(2026, 7, 26, 3, 0, tzinfo=UTC),
    )

    result = service.run_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )

    run = repository.get_run(result.run_id)
    revision = repository.list_research_chains(instrument="7203.T")[0].current_revision
    expected = datetime(
        2026,
        7,
        24,
        23,
        59,
        59,
        999999,
        tzinfo=timezone(timedelta(hours=9)),
    )
    assert run.request.analysis_date == date(2026, 7, 24)
    assert run.information_frontier == expected
    assert revision is not None
    assert revision.cutoff == date(2026, 7, 24)
    assert revision.information_frontier == expected
    assert revision.evidence_snapshot.bundle.information_frontier == expected
    assert [item.source for item in revision.evidence_snapshot.bundle.items] == ["fixture"]


def test_current_research_execution_reuses_frozen_frontier_and_readiness_on_retry(
    app_settings,
    repository,
) -> None:
    clock = iter(
        (
            datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
            datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
        )
    )
    _Graph.error = RuntimeError("fixture failure after collection started")
    readiness_frontiers = []

    def readiness(*args, information_frontier, **kwargs):
        readiness_frontiers.append(information_frontier)
        return _anchor_ready(
            *args,
            information_frontier=information_frontier,
            **kwargs,
        )

    service = _service(
        app_settings,
        repository,
        utc_clock=lambda: next(clock),
        anchor_readiness_checker=readiness,
    )
    view = service.enqueue_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )
    claimed = repository.claim_run(view.id, "worker-1", app_settings.lease_seconds)
    with pytest.raises(RuntimeError, match="fixture failure"):
        service.execute_claimed(claimed, worker_id="worker-1")
    frozen = repository.get_run(view.id).information_frontier

    _Graph.error = None
    retried = service.retry(view.id)
    claimed = repository.claim_run(retried.id, "worker-2", app_settings.lease_seconds)
    service.execute_claimed(claimed, worker_id="worker-2")

    assert frozen == datetime(2026, 7, 24, 18, 0, tzinfo=timezone(timedelta(hours=9)))
    assert repository.get_run(view.id).information_frontier == frozen
    assert readiness_frontiers == [frozen]
    assert any(
        event.event_type == "research.anchor_readiness_reused"
        for event in repository.list_events(view.id)
    )


def test_readiness_failure_does_not_freeze_information_frontier(
    app_settings,
    repository,
) -> None:
    def not_ready(*_args):
        raise MarketDataNotReadyError("market not ready")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=not_ready,
        utc_clock=lambda: datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
    )
    view = service.enqueue_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )
    claimed = repository.claim_run(view.id, "worker", app_settings.lease_seconds)

    with pytest.raises(AnchorReadinessError) as raised:
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(view.id).information_frontier is None
    assert raised.value.result.reasons == (AnchorReadinessReason.MISSING_MARKET_OBSERVATION,)
    assert repository.get_run(view.id).metrics.llm_calls == 0


def test_anchor_readiness_failure_can_retry_and_freezes_only_successful_frontier(
    app_settings,
    repository,
) -> None:
    ready = False

    def readiness(*_args):
        if not ready:
            raise MarketDataNotReadyError("market not ready")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=readiness,
        utc_clock=lambda: datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
    )
    view = service.enqueue_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24")
    )
    claimed = repository.claim_run(view.id, "worker-1", app_settings.lease_seconds)
    with pytest.raises(AnchorReadinessError):
        service.execute_claimed(claimed, worker_id="worker-1")
    assert repository.get_run(view.id).information_frontier is None

    ready = True
    retried = service.retry(view.id)
    claimed = repository.claim_run(retried.id, "worker-2", app_settings.lease_seconds)
    result = service.execute_claimed(claimed, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert repository.get_run(view.id).information_frontier == datetime(
        2026, 7, 24, 18, 0, tzinfo=timezone(timedelta(hours=9))
    )
    assert result.metrics.llm_calls == 0
    assert result.metrics.tool_calls == 3


def test_initial_chain_can_explicitly_allow_non_anchor_full_research(
    app_settings,
    repository,
) -> None:
    readiness_calls = 0

    def readiness(*_args):
        nonlocal readiness_calls
        readiness_calls += 1
        raise AssertionError("non-anchor Full must not claim readiness")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=readiness,
        utc_clock=lambda: datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
    )

    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            anchor_readiness="allow_non_anchor",
        )
    )

    run = repository.get_run(result.run_id)
    assert result.status is RunStatus.SUCCEEDED
    assert run.request.anchor_readiness == "allow_non_anchor"
    assert readiness_calls == 0
    chain = service.list_research_chains(instrument="7203.T")[0]
    assert not chain.forward_research_anchor.is_forward_research_anchor
    assert chain.forward_research_anchor.reasons == ("anchor_readiness_not_required",)
    assert chain.next_update_policy == "full_required"
    assert any(
        event.event_type == "research.anchor_readiness_not_required"
        for event in repository.list_events(run.id)
    )


def test_successful_anchor_readiness_manifest_reaches_graph_context(
    app_settings,
    repository,
) -> None:
    readiness_results = []
    graph_contexts = []

    def readiness(*args, **kwargs):
        result = _anchor_ready(*args, **kwargs)
        readiness_results.append(result)
        return result

    class CapturingGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, **_kwargs):
            graph_contexts.append(context)
            return _execution(context.request.ticker)

    service = _service(
        app_settings,
        repository,
        graph_factory=CapturingGraph,
        anchor_readiness_checker=readiness,
    )

    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(readiness_results) == 1
    assert len(graph_contexts) == 1
    assert graph_contexts[0].anchor_readiness == readiness_results[0]


def test_required_evidence_gate_failure_retains_evidence_and_audit_event(
    app_settings,
    repository,
) -> None:
    class GateFailureGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, *, on_event, **_kwargs):
            execution = _execution(context.request.ticker)
            context.evidence_writer(execution.evidence)
            on_event(
                {
                    "event_type": "research.anchor_evidence_gate_failed",
                    "node": "evidence.seal",
                    "payload": {
                        "reason": "graph_visible_required_evidence_missing",
                        "missing_sources": ["EDINET"],
                        "missing_capabilities": ["official_filing"],
                    },
                }
            )
            raise GraphVisibleRequiredEvidenceError(
                missing_sources=("EDINET",),
                missing_capabilities=("official_filing",),
            )

    service = _service(
        app_settings,
        repository,
        graph_factory=GateFailureGraph,
    )
    queued = service.enqueue_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market", "news"),
        )
    )
    claimed = repository.claim_run(
        queued.id,
        "worker",
        app_settings.lease_seconds,
    )

    with pytest.raises(GraphVisibleRequiredEvidenceError):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert failed.metrics.llm_calls == 0
    assert repository.get_evidence(queued.id).instrument == "7203.T"
    events = repository.list_events(queued.id)
    gate_event = next(
        event
        for event in events
        if event.event_type == "research.anchor_evidence_gate_failed"
    )
    assert gate_event.payload == {
        "reason": "graph_visible_required_evidence_missing",
        "missing_sources": ["EDINET"],
        "missing_capabilities": ["official_filing"],
    }
    assert any(event.event_type == "run.failed" for event in events)


def test_required_evidence_gate_retry_reuses_manifest_and_sealed_bundle(
    app_settings,
    repository,
) -> None:
    readiness_calls = 0
    graph_calls = 0
    constructed_seals: list[datetime] = []
    observed_seals: list[datetime] = []

    def readiness(*args, **kwargs):
        nonlocal readiness_calls
        readiness_calls += 1
        return _anchor_ready(*args, **kwargs)

    class ReplayGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, *, on_event, **_kwargs):
            nonlocal graph_calls
            graph_calls += 1
            bundle = context.sealed_evidence_reader()
            if bundle is None:
                execution = _execution(context.request.ticker)
                seal = datetime(
                    2026,
                    7,
                    24,
                    10,
                    graph_calls,
                    tzinfo=UTC,
                )
                constructed_seals.append(seal)
                bundle = EvidenceBundle(
                    instrument=context.request.ticker,
                    analysis_date=context.request.analysis_date,
                    information_frontier=context.information_frontier,
                    items=execution.evidence.items,
                    sealed_at=seal,
                )
                context.evidence_writer(bundle)
            observed_seals.append(bundle.sealed_at)
            if graph_calls == 1:
                on_event(
                    {
                        "event_type": "research.anchor_evidence_gate_failed",
                        "node": "evidence.seal",
                        "payload": {
                            "reason": "graph_visible_required_evidence_missing",
                            "missing_sources": ["EDINET"],
                            "missing_capabilities": ["official_filing"],
                        },
                    }
                )
                raise GraphVisibleRequiredEvidenceError(
                    missing_sources=("EDINET",),
                    missing_capabilities=("official_filing",),
                )
            execution = _execution(context.request.ticker)
            return GraphExecution(
                state=execution.state,
                evidence=bundle,
                reports=execution.reports,
                decision=execution.decision,
            )

    service = _service(
        app_settings,
        repository,
        graph_factory=ReplayGraph,
        anchor_readiness_checker=readiness,
    )
    queued = service.enqueue_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker-1", app_settings.lease_seconds)
    with pytest.raises(GraphVisibleRequiredEvidenceError):
        service.execute_claimed(claimed, worker_id="worker-1")
    first_bundle = repository.get_evidence(queued.id)

    retried = service.retry(queued.id)
    claimed = repository.claim_run(retried.id, "worker-2", app_settings.lease_seconds)
    result = service.execute_claimed(claimed, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert readiness_calls == 1
    assert graph_calls == 2
    assert constructed_seals == [first_bundle.sealed_at]
    assert observed_seals == [first_bundle.sealed_at, first_bundle.sealed_at]
    assert repository.get_evidence(queued.id) == first_bundle
    assert any(
        event.event_type == "research.anchor_readiness_reused"
        for event in repository.list_events(queued.id)
    )


def test_full_update_that_must_reanchor_runs_complete_readiness_before_llm(
    app_settings,
    repository,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            anchor_readiness="allow_non_anchor",
        )
    )
    chain = repository.list_research_chains(instrument="7203.T")[0]
    llm_calls = 0
    observed_anchor_frontiers = []

    def llm_factory(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return object(), object()

    def not_ready(request, *, information_frontier, anchor_frontier, **_kwargs):
        observed_anchor_frontiers.append(anchor_frontier)
        return AnchorReadinessResult(
            ready=False,
            requested_cutoff=request.analysis_date,
            information_frontier=information_frontier,
            profile_id="jp-listed-equity-v1",
            reasons=(AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE,),
            metrics=RunMetrics(tool_calls=2),
        )

    service = _service(
        app_settings,
        repository,
        llm_factory=llm_factory,
        anchor_readiness_checker=not_ready,
    )
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-25"),
    )
    claimed = repository.claim_run(queued.id, "worker", app_settings.lease_seconds)

    with pytest.raises(AnchorReadinessError) as raised:
        service.execute_claimed(claimed, worker_id="worker")

    assert queued.research_execution_strategy == "full"
    assert raised.value.result.reasons == (
        AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE,
    )
    assert observed_anchor_frontiers == [chain.current_revision.information_frontier]
    assert llm_calls == 0


def test_source_frontier_and_structured_limitations_round_trip_without_optimism(
    app_settings,
    repository,
    tmp_path,
) -> None:
    frontier = datetime(2026, 7, 24, 18, 0, tzinfo=timezone(timedelta(hours=9)))

    def limited_assembler(request, execution):
        draft = _eligible_state_assembler(request, execution)
        watermark = draft.evidence_snapshot.source_watermarks[0].model_copy(
            update={
                "scanned_start": date(2026, 7, 1),
                "scanned_end": date(2026, 7, 23),
                "status": CoverageStatus.LIMITED,
                "limitations": ("Archive attests only through July 23.",),
            }
        )
        return draft.model_copy(
            update={
                "evidence_snapshot": draft.evidence_snapshot.model_copy(
                    update={
                        "source_watermarks": (
                            watermark,
                            *draft.evidence_snapshot.source_watermarks[1:],
                        )
                    }
                )
            }
        )

    service = _service(
        app_settings,
        repository,
        state_assembler=limited_assembler,
        utc_clock=lambda: frontier.astimezone(UTC),
    )

    result = service.run_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )
    revision = repository.list_research_chains(instrument="7203.T")[0].current_revision

    assert revision is not None
    watermark = revision.evidence_snapshot.source_watermarks[0]
    assert watermark.information_frontier == datetime(
        2026, 7, 23, 23, 59, 59, 999999, tzinfo=timezone(timedelta(hours=9))
    )
    assert watermark.information_frontier < revision.information_frontier
    assert watermark.requested_interval.model_dump(mode="json") == {
        "start": "2026-07-01",
        "end": "2026-07-23",
    }
    assert [item.model_dump(mode="json") for item in watermark.observed_intervals] == [
        {"start": "2026-07-01", "end": "2026-07-23"}
    ]
    assert watermark.structured_limitations[0].presentation_text == (
        "Archive attests only through July 23."
    )
    assert watermark.structured_limitations[0].temporal_scope == "point_in_time"
    assert repository.get_run(result.run_id).information_frontier == frontier
    _, markdown = service.export_revision(revision.id, format="markdown")
    assert "Research Cutoff: 2026-07-24" in markdown
    assert "Information Frontier: 2026-07-24T18:00:00+09:00" in markdown
    assert "source frontier: 2026-07-23T23:59:59.999999+09:00" in markdown
    _, exported_json = service.export_revision(revision.id, format="json")
    exported = json.loads(exported_json)
    assert exported["revision"]["information_frontier"] == "2026-07-24T18:00:00+09:00"
    backup = service.backup_database(tmp_path / "frontier-backup.db")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT information_frontier FROM runs WHERE id = ?",
            (result.run_id,),
        ).fetchone() == ("2026-07-24T18:00:00+09:00",)
        assert connection.execute(
            "SELECT information_frontier FROM research_revisions WHERE id = ?",
            (revision.id,),
        ).fetchone() == ("2026-07-24T18:00:00+09:00",)


def test_unavailable_source_does_not_claim_an_observed_frontier(
    app_settings,
    repository,
) -> None:
    def unavailable_assembler(request, execution):
        draft = _eligible_state_assembler(request, execution)
        watermark = draft.evidence_snapshot.source_watermarks[0].model_copy(
            update={
                "status": CoverageStatus.UNAVAILABLE,
                "limitations": ("Source was unavailable during collection.",),
            }
        )
        return draft.model_copy(
            update={
                "evidence_snapshot": draft.evidence_snapshot.model_copy(
                    update={
                        "source_watermarks": (
                            watermark,
                            *draft.evidence_snapshot.source_watermarks[1:],
                        )
                    }
                )
            }
        )

    service = _service(
        app_settings,
        repository,
        state_assembler=unavailable_assembler,
    )

    service.run_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )
    revision = repository.list_research_chains(instrument="7203.T")[0].current_revision

    assert revision is not None
    watermark = revision.evidence_snapshot.source_watermarks[0]
    assert watermark.status is CoverageStatus.UNAVAILABLE
    assert watermark.information_frontier is None
    assert watermark.observed_intervals == ()
    assert watermark.structured_limitations[0].observed_intervals == ()


def test_research_chain_market_readiness_fails_before_llm_or_graph(
    app_settings,
    repository,
) -> None:
    llm_calls = 0

    def llm_factory(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return (object(), object())

    def not_ready(*_args):
        raise RuntimeError("J-Quants daily bar is not ready")

    service = _service(
        app_settings,
        repository,
        llm_factory=llm_factory,
        market_data_readiness_checker=not_ready,
    )
    queued = service.enqueue_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-08-12",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(AnchorReadinessError) as raised:
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert failed.metrics.llm_calls == 0
    assert failed.metrics.tool_calls == 1
    assert raised.value.result.reasons == (AnchorReadinessReason.MISSING_MARKET_OBSERVATION,)
    assert llm_calls == 0


def test_research_chain_without_market_analyst_still_checks_jquants_readiness(
    app_settings,
    repository,
) -> None:
    readiness_calls = 0

    def not_ready(*_args):
        nonlocal readiness_calls
        readiness_calls += 1
        raise MarketDataNotReadyError("J-Quants daily bar is not ready")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=not_ready,
    )
    queued = service.enqueue_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-08-12",
            analysts=("fundamentals",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(AnchorReadinessError):
        service.execute_claimed(claimed, worker_id="worker")

    assert readiness_calls == 1
    assert repository.get_run(queued.id).status is RunStatus.FAILED


def test_service_translates_vendor_no_data_to_market_readiness_failure(
    app_settings,
    repository,
) -> None:
    def no_data(symbol, _cutoff):
        raise NoMarketDataError(symbol, detail="expected market bar is missing")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=no_data,
    )

    with pytest.raises(MarketDataNotReadyError, match="not ready") as raised:
        service.validate_market_data_readiness(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-08-12",
                analysts=("fundamentals",),
            )
        )

    assert isinstance(raised.value.__cause__, NoMarketDataError)


def test_ordinary_analysis_does_not_require_jquants_chain_readiness(
    app_settings,
    repository,
) -> None:
    readiness_calls = 0

    def readiness(*_args):
        nonlocal readiness_calls
        readiness_calls += 1
        raise AssertionError("ordinary analysis must not run chain readiness")

    service = _service(
        app_settings,
        repository,
        market_data_readiness_checker=readiness,
    )

    result = service.run(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-08-12",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert readiness_calls == 0


def test_successful_explicit_full_analysis_creates_primary_research_chain(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date="2026-07-24",
        analysts=("market",),
        output_language="ja",
    )

    result = service.run_initial_chain(request)

    chains = repository.list_research_chains(instrument="6501.T")
    assert len(chains) == 1
    chain = repository.get_research_chain(chains[0].id)
    assert chain.is_primary is True
    assert chain.instrument == "6501.T"
    assert len(chain.revisions) == 1
    revision = chain.current_revision
    assert revision is not None
    assert revision.producing_run_id == result.run_id
    assert revision.cutoff == date(2026, 7, 24)
    assert revision.execution_strategy.value == "full"
    assert revision.role is ResearchRevisionRole.INITIAL
    assert revision.change_conclusion is None
    assert revision.current_state.language == "ja"
    assert revision.evidence_snapshot.bundle.digest == result.evidence.digest
    assert revision.metrics == result.metrics
    assert revision.coverage.claims
    assert all(item.object_id for item in revision.coverage.claims)
    media_type, markdown = service.export_revision(
        revision.id,
        format="markdown",
    )
    assert media_type.startswith("text/markdown")
    assert "## Scenarios" in markdown
    assert "## Risks" in markdown
    assert "## Invalidation Conditions" in markdown
    assert "Fixture evidence." in markdown
    assert "## Execution Metrics" in markdown
    with (
        pytest.raises(DatabaseError, match="immutable"),
        repository.engine.begin() as connection,
    ):
        connection.execute(
            text(
                "UPDATE research_revisions SET change_conclusion = "
                "'no_material_change' WHERE id = :revision_id"
            ),
            {"revision_id": revision.id},
        )


def test_full_research_assembles_multiple_edinet_corrections_with_parent_outside_window(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    parent_id = "S100OUTSIDE"
    records = [
        {
            "secCode": "65010",
            "docDescription": "訂正有価証券報告書",
            "docTypeCode": "130",
            "filerName": "株式会社日立製作所",
            "submitDateTime": "2026-07-20 15:00",
            "opeDateTime": operation_time,
            "docID": doc_id,
            "parentDocID": parent_id,
            "docInfoEditStatus": "2",
        }
        for doc_id, operation_time in (
            ("S100CORRECTION1", "2026-07-24 09:15"),
            ("S100CORRECTION2", "2026-07-24 10:30"),
        )
    ]
    edinet_common._documents_cache.clear()
    monkeypatch.setattr(edinet_common, "fetch_documents", lambda _date: records)
    service = _service(
        app_settings,
        repository,
        graph_factory=_EdinetParentCorrectionGraph,
    )

    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    chain = repository.list_research_chains(instrument="6501.T")[0]
    revision = repository.get_research_chain(chain.id).current_revision
    assert revision is not None
    assert {item.record_id for item in revision.evidence_snapshot.source_records} == {
        parent_id,
        "market:6501.T",
    }
    assert {
        item.native_record_id
        for item in revision.evidence_snapshot.source_records
        if item.native_record_id is not None
    } == {
        "S100CORRECTION1",
        "S100CORRECTION2",
    }
    assert all(
        item.replaces_version_id is None for item in revision.evidence_snapshot.source_records
    )


def test_initial_research_chain_does_not_load_or_inject_legacy_memory(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    _MemoryCapturingGraph.memories = []

    def legacy_memory_must_not_load(*_args, **_kwargs):
        raise AssertionError("Research Chain execution loaded legacy memory")

    monkeypatch.setattr(repository, "memory_context", legacy_memory_must_not_load)
    service = _service(
        app_settings,
        repository,
        graph_factory=_MemoryCapturingGraph,
    )

    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(_MemoryCapturingGraph.memories) == 1
    assert _MemoryCapturingGraph.memories[0].items == ()


def test_ordinary_analysis_does_not_load_or_inject_legacy_memory(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    _MemoryCapturingGraph.memories = []

    def legacy_memory_must_not_load(*_args, **_kwargs):
        raise AssertionError("Ordinary execution loaded legacy memory")

    monkeypatch.setattr(repository, "memory_context", legacy_memory_must_not_load)
    service = _service(
        app_settings,
        repository,
        graph_factory=_MemoryCapturingGraph,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(_MemoryCapturingGraph.memories) == 1
    assert _MemoryCapturingGraph.memories[0].items == ()


def test_research_chain_update_and_full_comparison_do_not_load_legacy_memory(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_MemoryCapturingGraph,
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    _MemoryCapturingGraph.memories = []

    def legacy_memory_must_not_load(*_args, **_kwargs):
        raise AssertionError("Research Chain update loaded legacy memory")

    monkeypatch.setattr(repository, "memory_context", legacy_memory_must_not_load)
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.SUCCEEDED
    assert len(_MemoryCapturingGraph.memories) == 1
    assert _MemoryCapturingGraph.memories[0].items == ()
    assert repository.get_research_chain(chain.id).current_revision.sequence == 2


def test_feedback_failure_cannot_change_research_revision(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="NVDA")[0]
    revision_before = chain.current_revision
    outcome = repository.pending_outcomes(due_at=datetime(2100, 1, 1, tzinfo=UTC))[0]
    repository.persist_outcome_observation(
        outcome["outcome_id"],
        observation=OutcomeObservation(
            raw_return=0.03,
            alpha_return=0.01,
            holding_intervals=5,
            start_date=date(2026, 7, 25),
            end_date=date(2026, 8, 1),
        ),
        observed_at=datetime(2026, 8, 1, 20, tzinfo=UTC),
    )

    def fail_qualification(**_kwargs):
        raise RuntimeError("qualification failed")

    monkeypatch.setattr(
        "tradingagents.application.repository.qualify_reflection",
        fail_qualification,
    )
    with pytest.raises(RuntimeError, match="qualification failed"):
        repository.persist_generated_reflection(
            outcome["outcome_id"],
            draft=OutcomeReflectionDraft(
                directional_assessment="mixed",
                source_decision_evidence_lesson="Compare stored decision evidence.",
                method_lesson="Use a bounded methodological check.",
            ),
            generated_at=datetime(2026, 8, 1, 20, 1, tzinfo=UTC),
        )

    assert repository.get_run(result.run_id).status is RunStatus.SUCCEEDED
    revision_after = repository.get_research_chain(chain.id).current_revision
    assert revision_before is not None
    assert revision_after == revision_before
    assert revision_after.producing_run_id == result.run_id


def test_settlement_qualifies_decision_cutoff_as_versioned_feedback(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="NVDA")[0]
    revision_before = chain.current_revision
    clock = iter(
        (
            datetime(2100, 1, 1, tzinfo=UTC),
            datetime(2100, 1, 1, 0, 1, tzinfo=UTC),
            datetime(2100, 1, 1, 0, 2, tzinfo=UTC),
        )
    )
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        reflector=object(),
        utc_clock=lambda: next(clock),
    )
    monkeypatch.setattr(
        settlement,
        "observe",
        lambda *_args, **_kwargs: OutcomeObservation(
            raw_return=0.03,
            alpha_return=0.01,
            holding_intervals=5,
            start_date=date(2026, 7, 24),
            end_date=date(2026, 8, 1),
        ),
    )
    monkeypatch.setattr(
        settlement,
        "_reflection",
        lambda **_kwargs: OutcomeReflectionDraft(
            directional_assessment="mixed",
            source_decision_evidence_lesson="Compare stored decision evidence.",
            method_lesson="Use a bounded methodological check.",
        ),
    )

    stats = settlement.settle_once()

    feedback = repository.review_entries(ticker="NVDA")[0]["outcome_feedback"]
    assert stats == {"checked": 1, "resolved": 1, "pending": 0, "failed": 0}
    assert feedback["status"] == "eligible"
    assert feedback["qualification_policy_version"] == ("outcome_feedback_qualification.v2")
    assert repository.review_entries(ticker="NVDA")[0]["method_feedback"] == (
        "Use a bounded methodological check."
    )
    assert repository.get_run(result.run_id).status is RunStatus.SUCCEEDED
    assert repository.get_research_chain(chain.id).current_revision == revision_before


def test_full_update_is_idempotent_for_current_head_and_advances_atomically(
    app_settings,
    repository,
    tmp_path,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date="2026-07-25",
        analysts=("market",),
    )

    first = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        request,
    )
    duplicate = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        request,
    )
    assert duplicate.id == first.id
    assert duplicate.update_intent_id == first.update_intent_id
    assert duplicate.research_execution_strategy == "incremental"

    advance = repository.research_store.advance_research_chain

    def advance_inside_facade_transaction(session, **kwargs):
        assert session.in_transaction()
        return advance(session, **kwargs)

    monkeypatch.setattr(
        repository.research_store,
        "advance_research_chain",
        advance_inside_facade_transaction,
    )

    claimed = repository.claim_run(first.id, "worker", 30)
    result = service.execute_claimed(claimed, worker_id="worker")

    advanced = repository.get_research_chain(chain.id)
    assert result.status is RunStatus.SUCCEEDED
    assert advanced.current_revision_id != chain.current_revision_id
    assert [item.sequence for item in advanced.revisions] == [1, 2]
    assert advanced.current_revision.predecessor_revision_id == chain.current_revision_id
    assert advanced.current_revision.producing_run_id == first.id
    assert advanced.current_revision.delta.claims
    assert advanced.current_revision.update_summary.baseline_cutoff == date(2026, 7, 24)
    assert advanced.current_revision.update_summary.analysis_cutoff == date(2026, 7, 25)
    assert advanced.current_revision.update_summary.execution_strategy.value == "full"
    assert (
        advanced.current_revision.update_summary.change_conclusion
        == advanced.current_revision.change_conclusion
    )
    assert "Evidence items" in advanced.current_revision.update_summary.summary
    completed_duplicate = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        request,
    )
    assert completed_duplicate.id == first.id
    assert completed_duplicate.update_intent_id == first.update_intent_id
    media_type, body = service.export_revision(
        advanced.current_revision_id,
        format="json",
    )
    assert media_type == "application/json"
    assert json.loads(body)["revision"]["delta"]["claims"]

    backup = service.backup_database(tmp_path / "full-update.db")
    with sqlite3.connect(backup) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM research_revisions WHERE chain_id = ?",
                (chain.id,),
            ).fetchone()[0]
            == 2
        )
    assert connection.execute(
        "SELECT delta_json FROM research_revisions WHERE sequence = 2"
    ).fetchone()[0]


def test_full_update_disposes_questions_after_assembly_and_persists_audit(
    app_settings,
    repository,
) -> None:
    question = ResearchQuestion(
        id="question_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        question="Will orders remain durable?",
    )

    def state_assembler(request, execution):
        draft = _eligible_state_assembler(request, execution)
        if request.analysis_date == date(2026, 7, 24):
            return draft.model_copy(
                update={
                    "current_state": draft.current_state.model_copy(
                        update={"questions": (question,)}
                    ),
                    "coverage": draft.coverage.model_copy(
                        update={
                            "questions": (
                                ResearchObjectCoverage(
                                    object_id=question.id,
                                    status=CoverageStatus.COMPLETE,
                                ),
                            )
                        }
                    ),
                }
            )
        assert draft.current_state.questions == ()
        return draft

    def question_dispositioner(baseline, candidate, _llm):
        assert baseline.current_state.questions == (question,)
        assert candidate.current_state.questions == ()
        evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref
        return candidate.model_copy(
            update={
                "delta": candidate.delta.model_copy(
                    update={
                        "question_disposition": QuestionDispositionAudit(
                            status="complete",
                            language=candidate.current_state.language,
                            dispositions=(
                                QuestionDispositionRecord(
                                    baseline_question_id=question.id,
                                    disposition="answered",
                                    evidence_refs=(evidence_ref,),
                                    reason="The sealed Full Evidence answers the Question.",
                                ),
                            ),
                        )
                    }
                )
            }
        )

    service = _service(
        app_settings,
        repository,
        state_assembler=state_assembler,
        question_dispositioner=question_dispositioner,
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
        execution_strategy=ResearchExecutionStrategy.FULL,
    )

    result = service.execute_claimed(
        repository.claim_run(queued.id, "worker", 30),
        worker_id="worker",
    )

    revision = repository.get_research_chain(chain.id).current_revision
    retained = next(item for item in revision.current_state.questions if item.id == question.id)
    assert result.status is RunStatus.SUCCEEDED
    assert retained.status is QuestionStatus.ANSWERED
    assert retained.last_disposition == "answered"
    assert retained.disposition_reason == "The sealed Full Evidence answers the Question."
    assert revision.delta.question_disposition.status == "complete"
    event = next(
        item
        for item in repository.list_events(queued.id)
        if item.event_type == "research.question_disposition_completed"
    )
    assert event.payload == {
        "status": "complete",
        "limitation_reason": None,
        "repair_attempted": False,
    }
    media_type, markdown = service.export_revision(revision.id, format="markdown")
    assert media_type.startswith("text/markdown")
    assert "### Question Disposition Audit" in markdown
    assert "; disposition: answered" in markdown
    assert "The sealed Full Evidence answers the Question." in markdown
    _, exported_json = service.export_revision(revision.id, format="json")
    assert (
        json.loads(exported_json)["revision"]["delta"]["question_disposition"]["status"]
        == "complete"
    )


@pytest.mark.parametrize(
    ("mode", "ticker", "expected_strategy"),
    [
        ("off", "6501.T", "full"),
        ("shadow", "6501.T", "incremental"),
        ("experimental", "6501.T", "incremental"),
        ("experimental", "7203.T", "incremental"),
        ("experimental", "NVDA", "full"),
        ("experimental", "600309.SS", "full"),
    ],
)
def test_chain_update_strategy_uses_source_qualified_japanese_capability(
    app_settings,
    repository,
    mode,
    ticker,
    expected_strategy,
) -> None:
    configured = app_settings.model_copy(update={"research_update_mode": mode})
    service = _service(configured, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker=ticker, analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument=ticker)[0]

    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker=ticker, analysis_date="2026-07-25", analysts=("market",)),
    )

    assert queued.research_execution_strategy == expected_strategy
    assert queued.config_snapshot["research_update_mode"] == mode
    assert "experimental_nmc_jp_whitelist" not in queued.config_snapshot


def test_service_presents_persisted_chain_with_configured_source_qualified_policy(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="7203.T", analysis_date="2026-07-24", analysts=("market",))
    )

    persisted = repository.list_research_chains(instrument="7203.T")[0]
    assert persisted.current_revision.coverage.anchor_qualification.is_forward_research_anchor
    assert not persisted.forward_research_anchor.is_forward_research_anchor
    assert persisted.next_update_policy == "full_required"
    assert persisted.next_update_reason is None

    eligible = service.list_research_chains(instrument="7203.T")[0]
    assert eligible.forward_research_anchor.is_forward_research_anchor is True
    assert eligible.current_revision.coverage.anchor_qualification == (
        eligible.forward_research_anchor
    )
    _, exported = service.export_revision(
        eligible.current_revision_id,
        format="markdown",
    )
    assert "Forward Research Anchor: qualified" in exported
    assert "### Anchor Coverage" in exported
    assert eligible.next_update_policy == "incremental_allowed"
    assert eligible.next_update_reason is None

    off_repository = RunRepository(app_settings.model_copy(update={"research_update_mode": "off"}))
    full_only = _service(
        app_settings.model_copy(update={"research_update_mode": "off"}),
        off_repository,
    ).get_research_chain(eligible.id)
    assert full_only.next_update_policy == "full_required"
    assert full_only.next_update_reason == "experiment_mode_off"


def test_ineligible_head_rejects_explicit_incremental_but_allows_full(
    app_settings,
    repository,
) -> None:
    def incomplete_assembler(request, execution):
        draft = _eligible_state_assembler(request, execution)
        return draft.model_copy(
            update={
                "coverage": draft.coverage.model_copy(
                    update={
                        "domains": tuple(
                            item.model_copy(update={"status": CoverageStatus.LIMITED})
                            for item in draft.coverage.domains
                        ),
                        "supports_no_material_change": False,
                    }
                )
            }
        )

    service = _service(
        app_settings,
        repository,
        graph_factory=_FrontierCapturingGraph,
        state_assembler=incomplete_assembler,
        revision_comparator=lambda _id, _baseline, draft: ResearchRevisionDraft.model_validate(
            draft.model_copy(
                update={
                    "role": ResearchRevisionRole.UPDATE,
                    "change_conclusion": ResearchChangeConclusion.INDETERMINATE,
                    "indeterminate_reason": IndeterminateReason.COVERAGE_INCOMPLETE,
                }
            ).model_dump(mode="python")
        ),
    )
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = service.list_research_chains(instrument="6501.T")[0]
    assert chain.next_update_policy == "full_required"
    assert chain.next_update_reason == "anchor_coverage_incomplete"
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))

    with pytest.raises(
        InvalidResearchBaselineError,
        match="Forward Research Anchor does not allow Incremental Execution",
    ):
        service.enqueue_chain_update(
            chain.id,
            chain.current_revision_id,
            request,
            execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
        )

    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        request,
        execution_strategy=ResearchExecutionStrategy.FULL,
    )
    assert queued.research_execution_strategy == "full"
    service.state_assembler = _eligible_state_assembler
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    recovered = service.get_research_chain(chain.id)
    assert recovered.current_revision.coverage.anchor_qualification is not None
    assert (
        recovered.current_revision.coverage.anchor_qualification.is_forward_research_anchor
    ), recovered.current_revision.coverage.anchor_qualification.model_dump(mode="json")
    assert recovered.next_update_policy == "incremental_allowed"
    assert recovered.next_update_reason is None
    following = service.enqueue_chain_update(
        chain.id,
        recovered.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-26", analysts=("market",)),
    )
    assert following.research_execution_strategy == "incremental"


def test_current_non_anchor_head_cannot_be_bypassed_with_an_older_anchor(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    old_anchor_id = chain.current_revision_id
    service.state_assembler = assemble_full_revision
    queued = service.enqueue_chain_update(
        chain.id,
        old_anchor_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
        execution_strategy=ResearchExecutionStrategy.FULL,
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    service.execute_claimed(claimed, worker_id="worker")
    non_anchor = repository.get_research_chain(chain.id)
    assert non_anchor.current_revision_id != old_anchor_id
    assert non_anchor.next_update_policy == "full_required"

    historical_export = service.get_revision_export(old_anchor_id)
    assert historical_export.revision.id == old_anchor_id
    assert historical_export.revision.coverage.anchor_qualification.is_forward_research_anchor
    assert historical_export.chain.current_revision_id == non_anchor.current_revision_id
    assert historical_export.chain.forward_research_anchor.is_forward_research_anchor is False
    assert historical_export.chain.next_update_policy == "full_required"

    with pytest.raises(InvalidResearchBaselineError, match="current Research Chain head"):
        service.enqueue_chain_update(
            chain.id,
            old_anchor_id,
            AnalysisRequest(ticker="6501.T", analysis_date="2026-07-26", analysts=("market",)),
        )


def test_explicit_incremental_cannot_bypass_off_mode_or_unsupported_market(
    app_settings,
    repository,
) -> None:
    off_service = _service(
        app_settings.model_copy(update={"research_update_mode": "off"}),
        repository,
    )
    off_service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    jp_chain = repository.list_research_chains(instrument="6501.T")[0]

    with pytest.raises(InvalidResearchBaselineError, match="experiment_mode_off"):
        off_service.enqueue_chain_update(
            jp_chain.id,
            jp_chain.current_revision_id,
            AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
            execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
        )

    experimental = _service(
        app_settings.model_copy(update={"research_update_mode": "experimental"}),
        repository,
    )
    experimental.run_initial_chain(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24", analysts=("market",))
    )
    us_chain = repository.list_research_chains(instrument="NVDA")[0]
    with pytest.raises(InvalidResearchBaselineError, match="unsupported_incremental_market"):
        experimental.enqueue_chain_update(
            us_chain.id,
            us_chain.current_revision_id,
            AnalysisRequest(ticker="NVDA", analysis_date="2026-07-25", analysts=("market",)),
            execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
        )


def test_inconclusive_full_reassessment_advances_an_indeterminate_full_only_head(
    app_settings,
    repository,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    service = _service(
        app_settings,
        repository,
        graph_factory=_FrontierCapturingGraph,
        state_assembler=_eligible_state_assembler,
        revision_comparator=lambda _id, _baseline, draft: ResearchRevisionDraft.model_validate(
            draft.model_copy(
                update={
                    "role": ResearchRevisionRole.UPDATE,
                    "change_conclusion": ResearchChangeConclusion.INDETERMINATE,
                    "indeterminate_reason": IndeterminateReason.COVERAGE_INCOMPLETE,
                }
            ).model_dump(mode="python")
        ),
    )
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    advanced = service.get_research_chain(chain.id)
    revision = advanced.current_revision
    assert result.status is RunStatus.SUCCEEDED
    assert revision.role is ResearchRevisionRole.UPDATE
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    assert revision.change_conclusion is ResearchChangeConclusion.INDETERMINATE
    assert revision.indeterminate_reason.value == "coverage_incomplete"
    assert advanced.forward_research_anchor.is_forward_research_anchor is True
    assert advanced.next_update_policy == "incremental_allowed"
    assert advanced.next_update_reason is None
    assert revision.research_update_audit.comparison == "not_applicable"

    next_run = service.enqueue_chain_update(
        advanced.id,
        revision.id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-26", analysts=("market",)),
    )
    assert next_run.research_execution_strategy == "incremental"


@pytest.mark.parametrize(
    ("full_conclusion", "indeterminate_reason", "expected_comparison"),
    [
        (ResearchChangeConclusion.NO_MATERIAL_CHANGE, None, "agreement"),
        (
            ResearchChangeConclusion.INDETERMINATE,
            IndeterminateReason.COVERAGE_INCOMPLETE,
            "inconclusive",
        ),
    ],
)
def test_shadow_quiet_update_retains_candidate_and_full_remains_authoritative(
    app_settings,
    repository,
    full_conclusion,
    indeterminate_reason,
    expected_comparison,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    candidate = baseline.model_copy(
        update={
            "cutoff": date(2026, 7, 25),
            "role": ResearchRevisionRole.UPDATE,
            "execution_strategy": ResearchExecutionStrategy.INCREMENTAL,
            "change_conclusion": ResearchChangeConclusion.NO_MATERIAL_CHANGE,
            "current_state": baseline.current_state.model_copy(
                update={"cutoff": date(2026, 7, 25)}
            ),
            "update_summary": baseline.update_summary.model_copy(
                update={
                    "summary": "Deterministic gates found no material change.",
                    "baseline_cutoff": baseline.cutoff,
                    "analysis_cutoff": date(2026, 7, 25),
                    "execution_strategy": ResearchExecutionStrategy.INCREMENTAL,
                    "change_conclusion": ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                }
            ),
        }
    )

    def quiet_gate(_baseline, _request, _config, _cancel_requested):
        return IncrementalGateResult(
            candidate=candidate,
            transition_coverage=_transition_coverage(
                _baseline,
                _request.analysis_date,
                complete=True,
            ),
            metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.25),
        )

    shadow_service = _service(
        app_settings,
        repository,
        incremental_gate=quiet_gate,
        revision_comparator=lambda _id, _baseline, draft: draft.model_copy(
            update={
                "role": ResearchRevisionRole.UPDATE,
                "change_conclusion": full_conclusion,
                "indeterminate_reason": indeterminate_reason,
            }
        ),
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = shadow_service.execute_claimed(claimed, worker_id="worker")

    advanced = repository.get_research_chain(chain.id)
    revision = advanced.current_revision
    audit = repository.get_run(queued.id).research_update_audit
    assert result.status is RunStatus.SUCCEEDED
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    assert revision.producing_run_id == queued.id
    assert audit is not None
    assert audit.candidate is not None
    assert audit.candidate.change_conclusion == "no_material_change"
    assert audit.authoritative_strategy == "full"
    assert audit.comparison == expected_comparison
    assert audit.escalation_reason is None
    assert audit.bounded_metrics.tool_calls == 2
    assert audit.full_metrics.llm_calls == result.metrics.llm_calls
    assert revision.metrics.tool_calls == result.metrics.tool_calls == 4
    events = repository.list_events(queued.id)
    assert [event.event_type for event in events] == [
        "run.queued",
        "run.started",
        "research.anchor_readiness_succeeded",
        "research.incremental_assessed",
        "research.shadow_full_started",
        "node.completed",
        "evidence.sealed",
        "research.shadow_compared",
        "run.succeeded",
    ]
    assessed = next(
        event for event in events if event.event_type == "research.incremental_assessed"
    )
    assert assessed.payload["coverage"] == audit.coverage.model_dump(mode="json")
    assert assessed.payload["evidence_lineage"] == [
        item.model_dump(mode="json") for item in audit.evidence_lineage
    ]
    assert assessed.payload["candidate_update_summary"] == (
        audit.candidate.update_summary.model_dump(mode="json")
    )


def test_experimental_quiet_update_advances_with_nmc_without_full_analysis(
    app_settings,
    repository,
    tmp_path,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    cutoff = date(2026, 7, 25)
    candidate = _experimental_nmc_candidate(baseline, cutoff)
    assert validate_experimental_nmc_candidate(baseline, candidate) is None

    class FullAnalysisMustNotRun:
        def __init__(self, **_kwargs):
            raise AssertionError("Full Analysis must not be constructed for experimental NMC")

    experimental_settings = app_settings.model_copy(update={"research_update_mode": "experimental"})
    gate_calls = 0

    def retrying_gate(*_args):
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 1:
            return IncrementalGateResult(
                escalation_reason=IncrementalEscalationReason.COVERAGE_INCOMPLETE,
                metrics=RunMetrics(tool_calls=3, wall_time_seconds=0.2),
            )
        return IncrementalGateResult(
            candidate=candidate,
            transition_coverage=_transition_coverage(baseline, cutoff, complete=True),
            metrics=RunMetrics(
                llm_calls=1,
                tool_calls=2,
                input_tokens=80,
                output_tokens=20,
                wall_time_seconds=0.4,
            ),
        )

    service = _service(
        experimental_settings,
        repository,
        graph_factory=_MetricFailureGraph,
        incremental_gate=retrying_gate,
    )
    queued = service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
    )
    duplicate = service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
    )
    first_claim = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(RuntimeError, match="fixture structured output failure"):
        service.execute_claimed(first_claim, worker_id="worker")
    assert repository.get_research_chain(chain.id).current_revision_id == baseline.id
    service.graph_factory = FullAnalysisMustNotRun
    retried = service.retry(queued.id)
    claimed = repository.claim_run(retried.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    revision = repository.get_research_chain(chain.id).current_revision
    audit = repository.get_run(queued.id).research_update_audit
    assert duplicate.id == queued.id
    assert result.status is RunStatus.SUCCEEDED
    assert result.reports == {}
    assert result.decision is None
    assert revision.execution_strategy is ResearchExecutionStrategy.INCREMENTAL
    assert revision.change_conclusion is ResearchChangeConclusion.NO_MATERIAL_CHANGE
    assert revision.current_state.opinion == baseline.current_state.opinion
    assert revision.current_state.cutoff == cutoff
    assert all(item.change is ClaimChange.REAFFIRMED for item in revision.delta.claims)
    assert all(item.lineage == "inherited" for item in revision.evidence_snapshot.lineage)
    assert audit is not None
    assert audit.mode == "experimental"
    assert audit.authoritative_strategy == "incremental"
    assert audit.comparison == "not_applicable"
    assert revision.metrics == result.metrics
    assert revision.metrics.llm_calls == 2
    assert revision.metrics.tool_calls == 7
    assert audit.bounded_metrics.llm_calls == 1
    assert audit.bounded_metrics.tool_calls == 5
    assert audit.full_metrics.llm_calls == 1
    events = [event.event_type for event in repository.list_events(queued.id)]
    assert "run.failed" in events
    assert "run.retry_queued" in events
    assert events[-3:] == [
        "evidence.sealed",
        "research.experimental_nmc_committed",
        "run.succeeded",
    ]
    media_type, exported = service.export_revision(revision.id, format="json")
    assert media_type == "application/json"
    assert json.loads(exported)["revision"]["research_update_audit"]["mode"] == "experimental"
    backup = service.backup_database(tmp_path / "experimental-nmc.db")
    with sqlite3.connect(backup) as connection:
        stored = connection.execute(
            "SELECT research_update_audit_json FROM research_revisions WHERE id = ?",
            (revision.id,),
        ).fetchone()[0]
    assert json.loads(stored)["authoritative_strategy"] == "incremental"

    presented = service.get_research_chain(chain.id)
    assert presented.forward_research_anchor.is_forward_research_anchor is True
    assert presented.current_revision.coverage.anchor_qualification == (
        presented.forward_research_anchor
    )
    assert presented.next_update_policy == "incremental_allowed"
    assert presented.next_update_reason is None


def test_experimental_incomplete_transition_refuses_authoritative_nmc(
    app_settings,
    repository,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    cutoff = date(2026, 7, 25)
    candidate = _experimental_nmc_candidate(baseline, cutoff)
    full_calls = 0

    class CountingGraph(_Graph):
        def execute(self, context, **kwargs):
            nonlocal full_calls
            full_calls += 1
            return super().execute(context, **kwargs)

    experimental_service = _service(
        app_settings.model_copy(update={"research_update_mode": "experimental"}),
        repository,
        graph_factory=CountingGraph,
        incremental_gate=lambda *_args: IncrementalGateResult(
            candidate=candidate,
            transition_coverage=_transition_coverage(baseline, cutoff, complete=False),
            metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.2),
        ),
    )
    queued = experimental_service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = experimental_service.execute_claimed(claimed, worker_id="worker")

    revision = repository.get_research_chain(chain.id).current_revision
    audit = repository.get_run(queued.id).research_update_audit
    assert result.status is RunStatus.SUCCEEDED
    assert full_calls == 1
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    assert audit is not None
    assert audit.authoritative_strategy == "full"
    assert audit.escalation_reason == "coverage_incomplete"
    assert audit.transition_coverage is not None
    assert audit.transition_coverage.complete is False


@pytest.mark.parametrize(
    "malformation",
    ["empty_capabilities", "wrong_anchor", "wrong_update", "stale_intervals"],
)
def test_experimental_malformed_complete_transition_refuses_authoritative_nmc(
    app_settings,
    repository,
    malformation,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    cutoff = date(2026, 7, 25)
    candidate = _experimental_nmc_candidate(baseline, cutoff)
    transition = _transition_coverage(baseline, cutoff, complete=True)
    if malformation == "empty_capabilities":
        transition = transition.model_copy(update={"capabilities": ()})
    elif malformation == "wrong_anchor":
        transition = transition.model_copy(
            update={"anchor_frontier": baseline.information_frontier - timedelta(seconds=1)}
        )
    elif malformation == "wrong_update":
        transition = transition.model_copy(
            update={"update_frontier": transition.update_frontier + timedelta(seconds=1)}
        )
    else:
        stale = SourceObservationInterval(start=date(2020, 1, 1), end=date(2020, 1, 1))
        transition = transition.model_copy(
            update={
                "capabilities": tuple(
                    item.model_copy(update={"checked_intervals": (stale,)})
                    for item in transition.capabilities
                )
            }
        )
    service = _service(
        app_settings.model_copy(update={"research_update_mode": "experimental"}),
        repository,
        incremental_gate=lambda *_args: IncrementalGateResult(
            candidate=candidate,
            transition_coverage=transition,
        ),
    )
    queued = service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    revision = repository.get_research_chain(chain.id).current_revision
    assert audit is not None
    assert audit.escalation_reason == "coverage_incomplete"
    assert audit.authoritative_strategy == "full"
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL


@pytest.mark.parametrize("transition_complete", [None, False])
def test_shadow_unproven_transition_retains_typed_escalation_and_runs_full(
    app_settings,
    repository,
    transition_complete,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    cutoff = date(2026, 7, 25)
    candidate = _experimental_nmc_candidate(baseline, cutoff)
    full_calls = 0

    class CountingGraph(_Graph):
        def execute(self, context, **kwargs):
            nonlocal full_calls
            full_calls += 1
            return super().execute(context, **kwargs)

    shadow_service = _service(
        app_settings,
        repository,
        graph_factory=CountingGraph,
        incremental_gate=lambda *_args: IncrementalGateResult(
            candidate=candidate,
            transition_coverage=(
                None
                if transition_complete is None
                else _transition_coverage(baseline, cutoff, complete=transition_complete)
            ),
            metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.2),
        ),
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date=cutoff, analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = shadow_service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    revision = repository.get_research_chain(chain.id).current_revision
    assert result.status is RunStatus.SUCCEEDED
    assert full_calls == 1
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    assert audit is not None
    assert audit.authoritative_strategy == "full"
    assert audit.candidate is not None
    assert audit.escalation_reason == "coverage_incomplete"
    assert (audit.transition_coverage is None) is (transition_complete is None)
    assert audit.coverage is not None
    assert audit.checked_windows
    assert audit.evidence_lineage
    assert audit.comparison in {"agreement", "inconclusive", "disagreement"}


def test_default_shadow_collection_runs_before_any_full_llm_client(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    llm_calls = 0
    collector_calls = 0

    def llm_factory(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return object(), object()

    def route_to_vendor(*_args, **_kwargs):
        nonlocal collector_calls
        assert llm_calls == 0
        collector_calls += 1
        return ""

    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        route_to_vendor,
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=llm_factory,
        graph_factory=_Graph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    llm_calls = 0
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert collector_calls == 1
    assert llm_calls == 1
    assert audit is not None
    assert audit.escalation_reason == "coverage_incomplete"
    assert audit.transition_coverage is not None
    assert audit.transition_coverage.complete is False
    assert audit.transition_coverage.anchor_frontier == chain.current_revision.information_frontier
    assert (
        audit.transition_coverage.update_frontier
        == repository.get_run(queued.id).information_frontier
    )
    assert all(
        capability.gaps
        for capability in audit.transition_coverage.capabilities
        if not capability.complete
    )
    assert audit.bounded_metrics.tool_calls == 1

    stored_transition = ResearchUpdateTransitionCoverage.model_validate(
        audit.transition_coverage.model_dump(mode="json")
    )
    revision = repository.get_research_chain(chain.id).current_revision
    audited_revision = revision.model_copy(
        update={
            "research_update_audit": audit.model_copy(
                update={"transition_coverage": stored_transition}
            )
        }
    )
    markdown = render_revision_export_markdown(
        service.get_revision_export(revision.id).model_copy(update={"revision": audited_revision})
    )
    assert "### Transition Coverage" in markdown
    assert "Frontier interval:" in markdown


def test_default_bounded_collection_omits_unattested_fallback_content(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    fallback_calls = 0

    def route_to_vendor(*_args, **_kwargs):
        nonlocal fallback_calls
        fallback_calls += 1
        return "POST FRONTIER FALLBACK"

    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        route_to_vendor,
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert fallback_calls == 1
    assert audit is not None
    assert audit.escalation_reason == "coverage_incomplete"
    assert audit.candidate is None
    revision = repository.get_research_chain(chain.id).current_revision
    assert all(
        "POST FRONTIER FALLBACK" not in (item.content or "")
        for item in revision.evidence_snapshot.bundle.items
    )
    assert audit.transition_coverage is not None, audit.model_dump(mode="json")
    disclosure = next(
        item
        for item in audit.transition_coverage.capabilities
        if item.capability == "timely_disclosure"
    )
    assert disclosure.complete is False
    assert disclosure.limitations[0].kind == "unavailable"


def test_default_bounded_collection_keeps_required_news_attestation_with_advisory_media(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    frontier = "2026-07-25T23:59:59.999999+09:00"

    def official_block(source):
        return attach_source_watermarks(
            f"## {source} disclosures\n\n### Attested item",
            SourceWatermark(
                source=source,
                scanned_start="2026-07-24",
                scanned_end="2026-07-25",
                status="complete",
                returned_records=0,
                reported_records=0,
                information_frontier=frontier,
            ),
        )

    monkeypatch.setattr(
        jp_news,
        "_edinet_news",
        lambda *_args, **_kwargs: official_block("EDINET"),
    )
    monkeypatch.setattr(
        jp_news,
        "_tdnet_news",
        lambda *_args, **_kwargs: official_block("TDnet"),
    )
    monkeypatch.setattr(
        jp_news,
        "_google_news",
        lambda *_args, **_kwargs: "No Google News items found.",
    )
    payload = jp_news.get_news(
        "6501.T",
        "2026-07-24",
        "2026-07-25",
        information_frontier=frontier,
    )
    google = next(
        item for item in extract_source_watermarks(payload) if item.source == "Google News"
    )
    assert google.temporal_scope == "live_only"
    assert google.information_frontier is None
    assert any(
        item.source == "EDINET" and item.information_frontier == frontier
        for item in extract_source_watermarks(payload)
    )
    assert any(
        item.source == "TDnet" and item.information_frontier == frontier
        for item in extract_source_watermarks(payload)
    )
    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        lambda *_args, **_kwargs: payload,
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_FrontierCapturingGraph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
        utc_clock=lambda: datetime.fromisoformat(frontier).astimezone(UTC),
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert audit is not None
    assert {item.source for item in audit.checked_windows}.issuperset(
        {
            "EDINET",
            "TDnet",
            "Google News",
        }
    )
    assert (
        next(
            item
            for item in audit.transition_coverage.capabilities
            if item.capability == "timely_disclosure"
        ).complete
        is True
    )


def test_default_bounded_collection_preserves_other_sources_when_one_required_source_fails(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    frontier = "2026-07-25T23:59:59.999999+09:00"
    payload = attach_provenance(
        attach_source_observations(
            attach_source_watermarks(
                "Mixed assembler payload. EDINET-FAILED-SENTINEL TDNET-SUCCESS-SENTINEL",
                SourceWatermark(
                    source="EDINET",
                    scanned_start="2026-07-24",
                    scanned_end="2026-07-25",
                    status="complete",
                    returned_records=0,
                    reported_records=0,
                ),
                SourceWatermark(
                    source="TDnet",
                    scanned_start="2026-07-24",
                    scanned_end="2026-07-25",
                    status="complete",
                    returned_records=0,
                    reported_records=0,
                    information_frontier=frontier,
                ),
                SourceWatermark(
                    source="Google News",
                    scanned_start="2026-07-24",
                    scanned_end="2026-07-25",
                    status="complete",
                    temporal_scope="live_only",
                    returned_records=0,
                    reported_records=0,
                ),
            ),
            SourceObservation(
                source="EDINET",
                record_id="edinet-failed",
                version_id="edinet-failed-v1",
                status="published",
                published_at="2026-07-25 12:00",
                available_at="2026-07-25T12:00:00+09:00",
                title="EDINET failed sentinel",
            ),
            SourceObservation(
                source="TDnet",
                record_id="tdnet-success",
                version_id="tdnet-success-v1",
                status="published",
                published_at="2026-07-25 13:00",
                available_at="2026-07-25T13:00:00+09:00",
                title="TDnet success sentinel",
            ),
        ),
        ProvenanceRecord(
            evidence="EDINET available sentinel",
            source="EDINET",
            requested="2026-07-24 to 2026-07-25",
            effective="2026-07-25",
            timing="available",
        ),
        ProvenanceRecord(
            evidence="TDnet available sentinel",
            source="TDnet",
            requested="2026-07-24 to 2026-07-25",
            effective="2026-07-25",
            timing="available",
        ),
    )
    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        lambda *_args, **_kwargs: payload,
    )
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_FrontierCapturingGraph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
        utc_clock=lambda: datetime.fromisoformat(frontier).astimezone(UTC),
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert audit is not None
    windows = {item.source: item for item in audit.checked_windows}
    assert windows["EDINET"].status == "unavailable"
    assert windows["TDnet"].status == "complete"
    assert windows["TDnet"].information_frontier == datetime.fromisoformat(frontier)
    assert windows["Google News"].temporal_scope == "live_only"
    revision = repository.get_research_chain(chain.id).current_revision
    records = revision.evidence_snapshot.source_records
    assert all(item.source != "EDINET" for item in records)
    assert any(item.source == "TDnet" and item.record_id == "tdnet-success" for item in records)
    assert all(
        "EDINET-FAILED-SENTINEL" not in (item.content or "")
        for item in revision.evidence_snapshot.bundle.items
    )
    origins = tuple(
        origin for item in revision.evidence_snapshot.bundle.items for origin in item.origins
    )
    assert not any(origin.evidence_type == "EDINET available sentinel" for origin in origins)
    assert any(
        origin.source == "EDINET"
        and origin.evidence_type == "bounded source collection"
        and origin.quality == "unavailable"
        for origin in origins
    )
    assert any(origin.evidence_type == "TDnet available sentinel" for origin in origins)


def test_service_preserves_same_day_jquants_adapter_availability_at_frozen_frontier(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    frontier = datetime(2026, 7, 27, 18, 0, tzinfo=timezone(timedelta(hours=9)))
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_FrontierCapturingGraph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
        utc_clock=lambda: frontier.astimezone(UTC),
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    dates = pd.bdate_range(end="2026-07-27", periods=220)
    frame = pd.DataFrame(
        {
            "Date": dates,
            "Open": range(220),
            "High": range(1, 221),
            "Low": range(220),
            "Close": range(1, 221),
            "Volume": range(100, 320),
        }
    )
    frame.attrs["price_adjustment"] = "J-Quants adjusted OHLCV v2"
    monkeypatch.setattr(jquants_indicator, "_fetch_ohlcv_frame", lambda *_args: frame)

    def bounded_route(method, *args, **kwargs):
        kwargs.pop("_provenance", None)
        if method == "get_verified_market_snapshot":
            return jquants_indicator.get_verified_market_snapshot(*args, **kwargs)
        information_frontier = kwargs["information_frontier"]
        return attach_source_watermarks(
            "No bounded disclosures were returned.",
            *(
                SourceWatermark(
                    source=source,
                    scanned_start="2026-07-24",
                    scanned_end="2026-07-27",
                    status="complete",
                    returned_records=0,
                    reported_records=0,
                    information_frontier=information_frontier,
                )
                for source in ("EDINET", "TDnet", "Google News")
            ),
        )

    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        bounded_route,
    )
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-27", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    run = repository.get_run(queued.id)
    audit = run.research_update_audit
    assert run.information_frontier == frontier
    assert audit is not None
    market_window = next(
        item for item in audit.checked_windows if item.source == "J-Quants adjusted OHLCV"
    )
    assert market_window.information_frontier == frontier
    market_coverage = next(
        item
        for item in audit.transition_coverage.capabilities
        if item.capability == "market_observation"
    )
    assert market_coverage.complete is True


@pytest.mark.parametrize(
    ("tdnet_start", "expected_complete"),
    [("2026-07-25", True), ("2026-07-26", False)],
)
def test_service_audits_same_tdnet_limitation_before_and_inside_transition(
    app_settings,
    repository,
    tdnet_start,
    expected_complete,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]

    def transition_gate(baseline, request, _config, _cancel_requested):
        records = [
            item.model_dump(mode="json", exclude={"evidence_ref", "fallback"})
            for item in baseline.evidence_snapshot.source_records
        ]
        records = [
            {
                **item,
                **(
                    {
                        "version_id": "market:2026-07-27",
                        "published_at": "2026-07-27",
                        "available_at": "2026-07-27T17:00:00+09:00",
                    }
                    if item.get("source") == "J-Quants adjusted OHLCV"
                    else {}
                ),
            }
            for item in records
        ]
        watermarks = []
        for item in baseline.evidence_snapshot.source_watermarks:
            raw = item.model_dump(
                mode="json",
                exclude={
                    "baseline_cutoff",
                    "overlap_start",
                    "information_frontier",
                    "structured_limitations",
                },
            )
            raw["scanned_end"] = request.analysis_date.isoformat()
            raw["requested_interval"] = {
                "start": "2026-07-01",
                "end": request.analysis_date.isoformat(),
            }
            raw["information_frontier"] = "2026-07-27T23:59:59.999999+09:00"
            if item.source == "TDnet":
                raw.update(
                    scanned_start=tdnet_start,
                    status="limited",
                    limitations=("Archive overlap was truncated.",),
                    limitation_kind="archive_truncation",
                    returned_records=0,
                    reported_records=0,
                )
            if item.source == "J-Quants adjusted OHLCV":
                raw.update(returned_records=1, reported_records=1)
            watermarks.append(raw)
        evidence = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            items=(
                EvidenceItem.create(
                    source="bounded fixture",
                    evidence_type="transition coverage",
                    requested_date=request.analysis_date,
                    effective_date=baseline.cutoff,
                    content="Checked transition sources.",
                    provenance={
                        "source_records": records,
                        "source_watermarks": watermarks,
                    },
                ),
            ),
        )
        return assess_deterministic_update(
            baseline.id,
            baseline,
            request,
            evidence,
            mode="shadow",
            information_frontier=datetime(
                2026,
                7,
                27,
                23,
                59,
                59,
                999999,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )

    service = _service(app_settings, repository, incremental_gate=transition_gate)
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-27", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert audit is not None
    assert audit.transition_coverage is not None
    assert audit.transition_coverage.complete is expected_complete, audit.model_dump(mode="json")
    assert audit.escalation_reason == (None if expected_complete else "coverage_incomplete")
    tdnet = next(
        item
        for item in audit.transition_coverage.capabilities
        if item.capability == "timely_disclosure"
    )
    assert tdnet.limitations[0].scope == ("pre_anchor" if expected_complete else "transition")


def test_service_retains_same_day_late_disclosure_as_new_transition_evidence(
    app_settings,
    repository,
) -> None:
    anchor_frontier = datetime(2026, 7, 24, 18, 0, tzinfo=timezone(timedelta(hours=9)))
    initial = _service(
        app_settings,
        repository,
        utc_clock=lambda: anchor_frontier.astimezone(UTC),
    )
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    assert chain.current_revision.information_frontier == anchor_frontier
    assert datetime.fromisoformat("2026-07-24T20:00:00+09:00") > anchor_frontier

    def late_event_gate(baseline, request, _config, _cancel_requested):
        market = next(
            item
            for item in baseline.evidence_snapshot.source_records
            if item.source == "J-Quants adjusted OHLCV"
        )
        records = [
            market.model_dump(mode="json", exclude={"evidence_ref", "fallback"}),
            {
                "source": "TDnet",
                "record_id": "tdnet-late",
                "version_id": "tdnet:late-same-day-service",
                "status": "published",
                "published_at": "2026-07-24 20:00",
                "available_at": "2026-07-24T20:00:00+09:00",
                "title": "Late same-day timely disclosure",
            },
        ]
        watermarks = [
            {
                "source": source,
                "scanned_start": "2026-07-24",
                "scanned_end": "2026-07-25",
                "status": "complete",
                "returned_records": 1 if source in {"TDnet", "J-Quants adjusted OHLCV"} else 0,
                "reported_records": 1 if source in {"TDnet", "J-Quants adjusted OHLCV"} else 0,
                "information_frontier": "2026-07-25T18:00:00+09:00",
            }
            for source in ("EDINET", "TDnet", "J-Quants adjusted OHLCV")
        ]
        evidence = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            items=(
                EvidenceItem.create(
                    source="bounded fixture",
                    evidence_type="same-day transition",
                    requested_date=request.analysis_date,
                    effective_date=date(2026, 7, 24),
                    content="Late same-day event.",
                    provenance={"source_records": records, "source_watermarks": watermarks},
                ),
            ),
        )
        return assess_deterministic_update(
            baseline.id,
            baseline,
            request,
            evidence,
            mode="shadow",
            information_frontier=datetime(2026, 7, 25, 18, 0, tzinfo=timezone(timedelta(hours=9))),
        )

    service = _service(app_settings, repository, incremental_gate=late_event_gate)
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert audit is not None
    assert audit.escalation_reason == "source_version_change"
    revision = repository.get_research_chain(chain.id).current_revision
    assert any(
        item.version_id == "tdnet:late-same-day-service"
        for item in revision.evidence_snapshot.source_records
    )
    late_lineage = next(
        item
        for item in revision.evidence_snapshot.source_record_lineage
        if item.version_id == "tdnet:late-same-day-service"
    )
    assert late_lineage.lineage == "new"
    assert late_lineage.observed_in_execution is True


def test_default_shadow_semantic_assessment_is_audited_before_independent_full(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
            output_language="ja",
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    payload = attach_source_observations(
        attach_source_watermarks(
            "No deterministic material change was found.",
            *(
                SourceWatermark(
                    source=source,
                    scanned_start="2026-07-01",
                    scanned_end="2026-07-25",
                    status="complete",
                    returned_records=(1 if source == "J-Quants adjusted OHLCV" else 0),
                    reported_records=(1 if source == "J-Quants adjusted OHLCV" else 0),
                    information_frontier="2026-07-25T23:59:59.999999+09:00",
                )
                for source in (
                    "EDINET",
                    "TDnet",
                    "Google News",
                    "J-Quants adjusted OHLCV",
                )
            ),
        ),
        SourceObservation(
            source="J-Quants adjusted OHLCV",
            record_id="market:6501.T",
            version_id="market:6501.T:2026-07-24",
            status="published",
            published_at="2026-07-24 17:00",
            available_at="2026-07-24T17:00:00+09:00",
            title="6501.T adjusted close",
            record_kind="market",
            adjustment="split_adjusted",
            unit="JPY",
        ),
    )
    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        lambda *_args, **_kwargs: payload,
    )
    llm_factory_calls = 0

    def llm_factory(_settings, *, callbacks):
        nonlocal llm_factory_calls
        llm_factory_calls += 1
        if llm_factory_calls == 1:
            semantic = _SemanticServiceLLM(callbacks[0])
            return RunLLMs(
                quick=semantic,
                deep=object(),
                quick_serializer=semantic,
                deep_serializer=object(),
            )
        return object(), object()

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=llm_factory,
        graph_factory=_Graph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
    )
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
            output_language="ja",
        ),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    revision = repository.get_research_chain(chain.id).current_revision
    assert result.status is RunStatus.SUCCEEDED
    assert llm_factory_calls == 2, audit.model_dump(mode="json") if audit else None
    assert audit is not None
    assert audit.semantic_assessment is not None
    assert audit.semantic_assessment.language == "ja"
    assert audit.semantic_assessment.relationships[0].relationship == "support"
    assert audit.candidate is not None
    assert audit.candidate.update_summary.summary == "既存の主張を再確認しました。"
    assert audit.bounded_metrics.llm_calls == 1
    assert audit.bounded_metrics.tool_calls == 2
    assert "research.incremental.semantic_assessment" in audit.bounded_metrics.node_metrics
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    media_type, exported = service.export_revision(revision.id, format="markdown")
    assert media_type.startswith("text/markdown")
    assert "### Semantic Change Assessment" in exported
    assert "既存の主張を再確認しました。" in exported
    assessed = next(
        event
        for event in repository.list_events(queued.id)
        if event.event_type == "research.incremental_assessed"
    )
    assert assessed.payload["semantic_assessment"]["language"] == "ja"


def test_cancelled_bounded_shadow_work_retains_audit_without_advancing_head(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]

    def cancelled_gate(_baseline, _request, _config, cancel_requested):
        assert cancel_requested() is True
        raise RunCancelled("cancelled during bounded collection")

    shadow_service = _service(
        app_settings,
        repository,
        incremental_gate=cancelled_gate,
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    shadow_service.cancel(queued.id)

    result = shadow_service.execute_claimed(claimed, worker_id="worker")

    run = repository.get_run(queued.id)
    assert result.status is RunStatus.CANCELLED
    assert run.research_update_audit is not None
    assert run.research_update_audit.comparison == "not_applicable"
    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id


def test_cancelled_after_partial_bounded_collection_retains_progress_and_metrics(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    shadow_service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        state_assembler=_eligible_state_assembler,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        market_data_readiness_checker=lambda *_args: None,
        anchor_readiness_checker=_anchor_ready,
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    payload = attach_source_observations(
        attach_source_watermarks(
            "No bounded source changes were returned.",
            *(
                SourceWatermark(
                    source=source,
                    scanned_start="2026-07-24",
                    scanned_end="2026-07-25",
                    status="complete",
                    returned_records=(1 if source == "J-Quants adjusted OHLCV" else 0),
                    reported_records=(1 if source == "J-Quants adjusted OHLCV" else 0),
                    information_frontier="2026-07-25T23:59:59.999999+09:00",
                )
                for source in (
                    "EDINET",
                    "TDnet",
                    "Google News",
                    "J-Quants fundamentals",
                    "J-Quants adjusted OHLCV",
                )
            ),
        ),
        SourceObservation(
            source="J-Quants adjusted OHLCV",
            record_id="market:6501.T",
            version_id="market:6501.T:2026-07-24",
            status="published",
            published_at="2026-07-24 17:00",
            available_at="2026-07-24T17:00:00+09:00",
            title="6501.T adjusted close",
            record_kind="market",
            adjustment="split_adjusted",
            unit="JPY",
        ),
    )

    def cancel_after_news(*_args, **_kwargs):
        repository.request_cancel(queued.id)
        return payload

    monkeypatch.setattr(
        "tradingagents.application.incremental.route_to_vendor",
        cancel_after_news,
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = shadow_service.execute_claimed(claimed, worker_id="worker")

    run = repository.get_run(queued.id)
    audit = run.research_update_audit
    assert audit is not None
    assert audit.escalation_reason is None, audit.model_dump(mode="json")
    assert result.status is RunStatus.CANCELLED, audit
    assert {item.source for item in audit.checked_windows} == {
        "EDINET",
        "TDnet",
        "Google News",
        "J-Quants fundamentals",
        "J-Quants adjusted OHLCV",
    }
    assert audit.bounded_metrics.tool_calls == 1
    assert run.metrics.tool_calls == 3
    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id


def test_failed_bounded_shadow_work_retains_audit_without_advancing_head(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]

    def failed_gate(*_args):
        raise RuntimeError("bounded fixture failure")

    shadow_service = _service(
        app_settings,
        repository,
        incremental_gate=failed_gate,
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(RuntimeError, match="bounded fixture failure"):
        shadow_service.execute_claimed(claimed, worker_id="worker")

    run = repository.get_run(queued.id)
    assert run.status is RunStatus.FAILED
    assert run.research_update_audit is not None
    assert run.research_update_audit.comparison == "not_applicable"
    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id


@pytest.mark.parametrize(
    ("graph_factory", "cancelled"),
    [(_MetricFailureGraph, False), (_MetricCancellationGraph, True)],
)
def test_shadow_terminal_full_path_retains_separate_partial_full_metrics(
    app_settings,
    repository,
    graph_factory,
    cancelled,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    shadow_service = _service(
        app_settings,
        repository,
        graph_factory=graph_factory,
        incremental_gate=lambda *_args: IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.COVERAGE_INCOMPLETE,
            metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.25),
        ),
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    if cancelled:
        result = shadow_service.execute_claimed(claimed, worker_id="worker")
        assert result.status is RunStatus.CANCELLED
    else:
        with pytest.raises(RuntimeError, match="fixture structured output failure"):
            shadow_service.execute_claimed(claimed, worker_id="worker")

    run = repository.get_run(queued.id)
    audit = run.research_update_audit
    assert audit is not None
    assert audit.bounded_metrics.tool_calls == 2
    assert audit.full_metrics.llm_calls == 1
    assert audit.full_metrics.input_tokens == 250
    assert run.metrics.tool_calls == 4
    assert run.metrics.llm_calls == 1
    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id


@pytest.mark.parametrize(
    "reason",
    [
        IncrementalEscalationReason.SOURCE_CORRECTION,
        IncrementalEscalationReason.SOURCE_WITHDRAWAL,
        IncrementalEscalationReason.COVERAGE_INCOMPLETE,
        IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS,
        IncrementalEscalationReason.THRESHOLD_CROSSING,
        IncrementalEscalationReason.POTENTIALLY_MATERIAL_NOVELTY,
        IncrementalEscalationReason.SEMANTIC_OUTPUT_INVALID,
        IncrementalEscalationReason.SEMANTIC_UNCERTAINTY,
    ],
)
def test_experimental_escalation_stops_bounded_work_and_runs_full(
    app_settings,
    repository,
    reason,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = repository.get_research_revision(chain.current_revision_id)
    full_calls = 0

    class CountingGraph(_Graph):
        def execute(self, context, **kwargs):
            nonlocal full_calls
            full_calls += 1
            return super().execute(context, **kwargs)

    def escalated_gate(_baseline, _request, _config, _cancel_requested):
        return IncrementalGateResult(
            escalation_reason=reason,
            coverage=_baseline.coverage,
            evidence_snapshot=_baseline.evidence_snapshot.model_copy(
                update={
                    "source_watermarks": (
                        SourceWatermarkSnapshot(
                            source="EDINET",
                            scanned_start=date(2026, 7, 1),
                            scanned_end=date(2026, 7, 25),
                            status="complete",
                            baseline_cutoff=_baseline.cutoff,
                            overlap_start=date(2026, 7, 1),
                        ),
                    )
                }
            ),
            metrics=RunMetrics(tool_calls=1, wall_time_seconds=0.1),
        )

    experimental_settings = app_settings.model_copy(update={"research_update_mode": "experimental"})
    experimental_service = _service(
        experimental_settings,
        repository,
        graph_factory=CountingGraph,
        incremental_gate=escalated_gate,
    )
    queued = experimental_service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = experimental_service.execute_claimed(claimed, worker_id="worker")

    audit = repository.get_run(queued.id).research_update_audit
    assert result.status is RunStatus.SUCCEEDED
    assert full_calls == 1
    assert audit is not None
    assert audit.mode == "experimental"
    assert audit.candidate is None
    assert audit.escalation_reason == reason.value
    assert audit.comparison == "not_applicable"
    assert audit.coverage is not None
    assert audit.checked_windows[0].source == "EDINET"
    assert audit.evidence_lineage
    assert audit.bounded_metrics.tool_calls == 1
    assert result.metrics.tool_calls == 3
    assert "research.full_escalation_started" in {
        event.event_type for event in repository.list_events(queued.id)
    }


@pytest.mark.parametrize("mode", ["shadow", "experimental"])
def test_dynamic_full_path_requires_anchor_readiness_before_llm_construction(
    app_settings,
    repository,
    mode,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = chain.current_revision
    gate_calls = 0
    observed_anchor_frontiers = []

    def escalated_gate(*_args):
        nonlocal gate_calls
        gate_calls += 1
        return IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.COVERAGE_INCOMPLETE
        )

    def not_ready(request, *, information_frontier, anchor_frontier, **_kwargs):
        observed_anchor_frontiers.append(anchor_frontier)
        return AnchorReadinessResult(
            ready=False,
            requested_cutoff=request.analysis_date,
            information_frontier=information_frontier,
            profile_id="jp-listed-equity-v1",
            reasons=(AnchorReadinessReason.REQUIRED_CAPABILITY_UNAVAILABLE,),
            metrics=RunMetrics(tool_calls=2),
        )

    service = _service(
        app_settings.model_copy(update={"research_update_mode": mode}),
        repository,
        incremental_gate=escalated_gate,
        anchor_readiness_checker=not_ready,
        llm_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("LLM construction must follow complete anchor readiness")
        ),
    )
    queued = service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(AnchorReadinessError):
        service.execute_claimed(claimed, worker_id="worker")

    assert observed_anchor_frontiers == [baseline.information_frontier]
    assert gate_calls == (0 if mode == "shadow" else 1)


def test_experimental_full_escalation_with_opt_out_remains_non_anchor(
    app_settings,
    repository,
) -> None:
    initial = _service(app_settings, repository)
    initial.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    baseline = chain.current_revision
    service = _service(
        app_settings.model_copy(update={"research_update_mode": "experimental"}),
        repository,
        incremental_gate=lambda *_args: IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.COVERAGE_INCOMPLETE
        ),
        anchor_readiness_checker=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit non-anchor Full must not claim readiness")
        ),
    )
    queued = service.enqueue_chain_update(
        chain.id,
        baseline.id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
            anchor_readiness="allow_non_anchor",
        ),
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    advanced = service.get_research_chain(chain.id)
    assert result.status is RunStatus.SUCCEEDED
    assert not advanced.forward_research_anchor.is_forward_research_anchor
    assert advanced.forward_research_anchor.reasons == ("anchor_readiness_not_required",)
    assert advanced.next_update_policy == "full_required"


def test_concurrent_full_update_submissions_resolve_to_one_execution(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date="2026-07-25",
        analysts=("market",),
    )
    barrier = Barrier(2)

    def submit():
        barrier.wait()
        return service.enqueue_chain_update(
            chain.id,
            chain.current_revision_id,
            request,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        submissions = tuple(executor.map(lambda _index: submit(), range(2)))

    assert len({item.id for item in submissions}) == 1
    assert len({item.update_intent_id for item in submissions}) == 1


@pytest.mark.parametrize("cutoff", ["2026-07-23", "2026-07-24"])
def test_full_update_rejects_non_later_cutoff(
    app_settings,
    repository,
    cutoff,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]

    with pytest.raises(ValueError, match="strictly later"):
        service.enqueue_chain_update(
            chain.id,
            chain.current_revision_id,
            AnalysisRequest(
                ticker="6501.T",
                analysis_date=cutoff,
                analysts=("market",),
            ),
        )


def test_failed_full_update_retries_the_same_intent_without_advancing_head(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
    )
    _Graph.error = RuntimeError("transient failure")
    claimed = repository.claim_run(queued.id, "worker", 30)
    with pytest.raises(RuntimeError, match="transient failure"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id
    intent_id = repository.get_run(queued.id).update_intent_id
    retried = service.retry(queued.id)
    assert retried.id == queued.id
    assert retried.update_intent_id == intent_id
    assert retried.attempt == 2

    _Graph.error = None
    claimed = repository.claim_run(queued.id, "worker", 30)
    service.execute_claimed(claimed, worker_id="worker")
    assert repository.get_research_chain(chain.id).current_revision_id != chain.current_revision_id


def test_cancelled_and_trashed_full_update_leaves_head_unchanged(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    queued = service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        ),
    )

    service.cancel(queued.id)
    repository.trash_runs((queued.id,))

    assert repository.get_research_chain(chain.id).current_revision_id == chain.current_revision_id
    assert len(repository.get_research_chain(chain.id).revisions) == 1


def test_failed_explicit_full_analysis_creates_no_chain_or_revision(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RuntimeError("full analysis failed")

    with pytest.raises(RuntimeError, match="full analysis failed"):
        service.run_initial_chain(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-07-24",
                analysts=("market",),
            )
        )

    assert repository.list_research_chains(instrument="6501.T") == ()


def test_cancelled_explicit_full_analysis_creates_no_chain_or_revision(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RunCancelled()

    result = service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.CANCELLED
    assert repository.list_research_chains(instrument="6501.T") == ()


def test_revision_owns_state_and_evidence_after_producing_run_is_purged(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    first = service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-25",
            analysts=("market",),
        )
    )
    chains = repository.list_research_chains(instrument="6501.T")
    assert len(chains) == 2
    assert sum(chain.is_primary for chain in chains) == 1
    revision_id = next(
        chain.current_revision_id
        for chain in chains
        if chain.current_revision.producing_run_id == first.run_id
    )

    repository.trash_runs((first.run_id,))
    repository.purge_expired_trash(cutoff=datetime.now(UTC) + timedelta(days=1))

    revision = repository.get_research_revision(revision_id)
    assert revision.producing_run_id is None
    assert revision.current_state.opinion.thesis == "Fixture thesis."
    assert revision.evidence_snapshot.bundle.items[0].content == "Fixture evidence."


def test_repository_round_trip_keeps_legacy_internal_source_dependency_readable(
    app_settings,
    repository,
) -> None:
    internal_ref = "ev_deadbeefdead"

    def legacy_state_assembler(request, execution):
        draft = _eligible_state_assembler(request, execution)
        claim = draft.current_state.claims[0].model_copy(
            update={"required_sources": (internal_ref,)}
        )
        return draft.model_copy(
            update={
                "current_state": draft.current_state.model_copy(
                    update={"claims": (claim, *draft.current_state.claims[1:])}
                )
            }
        )

    service = _service(
        app_settings,
        repository,
        state_assembler=legacy_state_assembler,
    )
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]

    revision = repository.get_research_revision(chain.current_revision_id)
    presented = service.get_research_chain(chain.id)

    assert revision.current_state.claims[0].required_sources == (internal_ref,)
    assert presented.next_update_policy == "full_required"
    assert presented.next_update_reason == "invalid_source_dependency"


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (
            {
                "short_name": "NVIDIA",
                "company_name": "NVIDIA Corporation",
                "long_name": "NVIDIA Corporation",
                "name": "Fallback name",
            },
            "NVIDIA",
        ),
        ({"company_name": "Toyota Motor Corporation"}, "Toyota Motor Corporation"),
        ({"long_name": "Mitsubishi Heavy Industries"}, "Mitsubishi Heavy Industries"),
        ({"name": "Fallback name"}, "Fallback name"),
        ({"short_name": " n/a ", "company_name": "  "}, None),
    ],
)
def test_service_persists_preferred_instrument_display_name(
    app_settings,
    repository,
    identity,
    expected,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: identity,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_name == expected
    assert repository.get_run(result.run_id).instrument_name == expected
    assert repository.get_result(result.run_id).instrument_name == expected


def test_service_persists_cutoff_safe_local_name_once(
    app_settings,
    repository,
) -> None:
    observed: list[tuple[str, str]] = []

    def local_name(ticker, analysis_date, _config):
        observed.append((ticker, analysis_date))
        return "トヨタ自動車"

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=lambda _ticker, _date: {"company_name": "Toyota Motor Corporation"},
        local_name_resolver=local_name,
    )

    result = service.run(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.instrument_local_name == "トヨタ自動車"
    assert repository.get_run(result.run_id).instrument_local_name == "トヨタ自動車"
    assert repository.get_result(result.run_id).instrument_local_name == "トヨタ自動車"
    assert observed == [("7203.T", "2026-07-24")]


def test_instrument_identity_failure_does_not_fail_research_run(
    app_settings,
    repository,
    caplog,
) -> None:
    def fail_identity(_ticker, _date):
        raise RuntimeError("private resolver details")

    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
        identity_resolver=fail_identity,
    )

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.instrument_name is None
    assert "RuntimeError" in caplog.text
    assert "private resolver details" not in caplog.text


def test_service_commits_artifact_and_event_before_callback(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
    )
    observed = []

    def callback(event):
        if event.event_type != "artifact.created":
            return
        artifacts = repository.list_artifacts(event.run_id)
        assert [artifact.id for artifact in artifacts] == [event.payload["artifact_id"]]
        persisted = repository.list_events(
            event.run_id,
            after_sequence=event.sequence - 1,
        )
        assert persisted[0] == event
        observed.append(event)

    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        ),
        on_event=callback,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert len(observed) == 1
    assert set(observed[0].payload) == {
        "artifact_id",
        "attempt",
        "stage",
        "role",
        "round",
        "schema_version",
        "prompt_version",
        "generation_method",
        "generation_observations",
        "content_type",
    }


def test_artifact_persistence_failure_fails_attempt_and_retains_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ArtifactGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    def fail_write(*_args, **_kwargs):
        raise OSError("artifact database unavailable")

    monkeypatch.setattr(repository, "append_artifact", fail_write)

    with pytest.raises(OSError, match="artifact database unavailable"):
        service.execute_claimed(claimed, worker_id="worker")

    assert repository.get_run(queued.id).status is RunStatus.FAILED
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_concurrent_runs_do_not_cross_provider_configuration(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.barrier = Barrier(2)
    requests = (
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="openai",
        ),
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
            llm_provider="deepseek",
            output_language="ja",
        ),
    )
    claimed = []
    for index, request in enumerate(requests):
        queued = service.enqueue(request)
        claimed.append(repository.claim_run(queued.id, f"worker-{index}", 30))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: service.execute_claimed(
                    pair[1],
                    worker_id=f"worker-{pair[0]}",
                ),
                enumerate(claimed),
            )
        )

    assert {result.status for result in results} == {RunStatus.SUCCEEDED}
    assert set(_Graph.observed) == {
        ("NVDA", "openai", "openai"),
        ("7203.T", "deepseek", "deepseek"),
    }


def test_failure_is_redacted_and_checkpoint_is_retained(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RuntimeError("provider token=private-value")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(RuntimeError):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert "private-value" not in failed.error_message
    assert repository.checkpoint_thread(queued.id) == checkpoint


def test_failure_persists_observed_metrics_and_emits_the_aggregate(
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_MetricFailureGraph,
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(RuntimeError, match="structured output failure"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    event = repository.list_events(queued.id)[-1]
    assert failed.metrics.llm_calls == 1
    assert failed.metrics.input_tokens == 250
    assert failed.metrics.output_tokens == 25
    assert failed.metrics.node_metrics["analyst.market.serialize.core"].llm_calls == 1
    assert event.event_type == "run.failed"
    assert event.payload["metrics"]["input_tokens"] == 250

    _, json_body = service.export(queued.id, format="json")
    json_payload = json.loads(json_body)
    assert json_payload["run"]["metrics"]["input_tokens"] == 250
    assert json_payload["attempts"][0]["status"] == "failed"
    assert json_payload["attempts"][0]["metrics"]["input_tokens"] == 250

    _, markdown_body = service.export(queued.id, format="markdown")
    assert "## Performance" in markdown_body
    assert "| 1 | failed |" in markdown_body
    assert "Input tokens: `250`" in markdown_body

    _, package_body = service.export(queued.id, format="package")
    with zipfile.ZipFile(io.BytesIO(package_body)) as archive:
        run_payload = json.loads(archive.read("run.json"))
    assert run_payload["attempts"][0]["status"] == "failed"
    assert run_payload["run"]["metrics"]["input_tokens"] == 250


def test_cooperative_cancel_cleans_checkpoint(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = RunCancelled("cancelled")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    cleared = []
    monkeypatch.setattr(service, "_clear_checkpoint", cleared.append)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    assert cleared == [repository.checkpoint_thread(queued.id)]
    assert repository.get_run(queued.id).status is RunStatus.CANCELLED


def test_worker_shutdown_requeues_run_and_preserves_checkpoint(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    _Graph.error = WorkerShutdown("fixture shutdown")
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker", 30)
    checkpoint = repository.checkpoint_thread(queued.id)

    with pytest.raises(WorkerShutdown):
        service.execute_claimed(
            claimed,
            worker_id="worker",
            shutdown_requested=lambda: True,
        )

    released = repository.get_run(queued.id)
    assert released.status is RunStatus.QUEUED
    assert released.attempt == 1
    assert repository.checkpoint_thread(queued.id) == checkpoint
    assert repository.list_events(queued.id)[-1].event_type == "run.interrupted"


def test_worker_shutdown_reuses_readiness_and_sealed_evidence_in_same_attempt(
    app_settings,
    repository,
) -> None:
    readiness_calls = 0
    graph_calls = 0
    observed_seals: list[datetime] = []

    def readiness(*args, **kwargs):
        nonlocal readiness_calls
        readiness_calls += 1
        return _anchor_ready(*args, **kwargs)

    class SameAttemptGraph:
        def __init__(self, **_kwargs):
            pass

        def execute(self, context, **_kwargs):
            nonlocal graph_calls
            graph_calls += 1
            bundle = context.sealed_evidence_reader()
            if bundle is None:
                execution = _execution(context.request.ticker)
                bundle = EvidenceBundle(
                    instrument=context.request.ticker,
                    analysis_date=context.request.analysis_date,
                    information_frontier=context.information_frontier,
                    items=execution.evidence.items,
                    sealed_at=datetime(
                        2026,
                        7,
                        24,
                        10,
                        0,
                        tzinfo=UTC,
                    ),
                )
                context.evidence_writer(bundle)
            observed_seals.append(bundle.sealed_at)
            if graph_calls == 1:
                raise WorkerShutdown("fixture shutdown after evidence seal")
            execution = _execution(context.request.ticker)
            return GraphExecution(
                state=execution.state,
                evidence=bundle,
                reports=execution.reports,
                decision=execution.decision,
            )

    service = _service(
        app_settings,
        repository,
        graph_factory=SameAttemptGraph,
        anchor_readiness_checker=readiness,
    )
    queued = service.enqueue_initial_chain(
        AnalysisRequest(
            ticker="7203.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    claimed = repository.claim_run(queued.id, "worker-1", 30)

    with pytest.raises(WorkerShutdown):
        service.execute_claimed(claimed, worker_id="worker-1")

    released = repository.get_run(queued.id)
    assert released.status is RunStatus.QUEUED
    assert released.attempt == 1
    first_bundle = repository.get_evidence(queued.id)

    claimed_again = repository.claim_run(queued.id, "worker-2", 30)
    result = service.execute_claimed(claimed_again, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert readiness_calls == 1
    assert graph_calls == 2
    assert observed_seals == [first_bundle.sealed_at, first_bundle.sealed_at]
    assert repository.get_evidence(queued.id) == first_bundle
    assert any(
        event.event_type == "research.anchor_readiness_reused"
        for event in repository.list_events(queued.id)
    )


def test_queued_cancel_is_terminal_and_emits_event(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24"))

    cancelled = service.cancel(queued.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert repository.list_events(queued.id)[-1].event_type == "run.cancelled"


def test_retry_resumes_real_langgraph_checkpoint_and_success_cleans_it(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_ResumableGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker-1", 30)

    with pytest.raises(RuntimeError, match="after first checkpoint"):
        service.execute_claimed(claimed, worker_id="worker-1")

    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        checkpoint_config = {"configurable": {"thread_id": checkpoint_thread}}
        assert saver.get_tuple(checkpoint_config) is not None

    retried = service.retry(queued.id)
    assert retried.attempt == 2
    assert repository.checkpoint_thread(queued.id) == checkpoint_thread
    claimed_again = repository.claim_run(queued.id, "worker-2", 30)
    result = service.execute_claimed(claimed_again, worker_id="worker-2")

    assert result.status is RunStatus.SUCCEEDED
    assert _ResumableGraph.first_calls == 1
    assert _ResumableGraph.second_calls == 2
    artifacts = repository.list_artifacts(queued.id)
    assert len(artifacts) == 1
    assert artifacts[0].attempt == 1
    assert "run.resumed" in {event.event_type for event in repository.list_events(queued.id)}
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple(checkpoint_config) is None


def test_cooperative_cancel_deletes_real_pending_checkpoint(
    app_settings,
    repository,
) -> None:
    service = AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_CancellingCheckpointGraph,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
    )
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    checkpoint_thread = repository.checkpoint_thread(queued.id)
    claimed = repository.claim_run(queued.id, "worker", 30)

    result = service.execute_claimed(claimed, worker_id="worker")

    assert result.status is RunStatus.CANCELLED
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        assert saver.get_tuple({"configurable": {"thread_id": checkpoint_thread}}) is None


@pytest.mark.parametrize("format", ("markdown", "json", "package"))
def test_service_export_reads_the_durable_result(
    format,
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        graph_factory=_ArtifactGraph,
    )
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    repository.append_event(
        result.run_id,
        "node.output_retry",
        node="debate.agenda.serialize",
        payload={
            "method": "tool_call_recovered",
            "reason_code": "non_json_response",
            "validation_issues": ["schema.issues"],
        },
    )
    repository.append_event(
        result.run_id,
        "node.output_recovered",
        node="debate.agenda.serialize",
        payload={"method": "tool_call_recovered"},
    )

    media_type, body = service.export(result.run_id, format=format)

    expected_media_type = {
        "markdown": "text/markdown",
        "json": "application/json",
        "package": "application/zip",
    }[format]
    assert media_type.startswith(expected_media_type)
    if format == "package":
        assert isinstance(body, bytes)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            report = archive.read("report.md").decode()
            assert result.run_id in report
            assert "Fixture thesis" in report
            run_payload = json.loads(archive.read("run.json"))
            assert run_payload["attempts"][0]["status"] == "succeeded"
            assert run_payload["result"]["recoveries"][0]["node"] == ("debate.agenda.serialize")
            assert "## Structured Recoveries" in report
        return
    assert isinstance(body, str)
    assert result.run_id in body
    assert "Fixture thesis" in body
    if format == "json":
        payload = json.loads(body)
        assert payload["schema_version"] == "9"
        assert payload["run"]["id"] == result.run_id
        assert payload["attempts"][0]["status"] == "succeeded"
        assert payload["attempts"][0]["metrics"] == payload["run"]["metrics"]
        assert payload["result"]["evidence"] == payload["evidence"]
        assert payload["result"]["recoveries"][0]["initial_reason_code"] == ("non_json_response")
        assert payload["evidence"]["items"][0]["source"] == "fixture"
        assert payload["artifacts"][0]["stage"] == "analyst"
        content = payload["artifacts"][0]["content"]
        assert "Fixture report." in content["markdown"]
    else:
        assert "## Research Process" in body
        assert "### analyst · market · round 0" not in body
        assert "## Reports" in body
        assert "Fixture report." in body
        assert "_No deliberation artifacts were recorded for this run._" in body
        assert "## Warnings" in body
        assert "## Structured Recoveries" in body
        assert "`debate.agenda.serialize`" in body
        assert "## Sources" in body
        assert "### Attempts" in body
        assert "| 1 | succeeded |" in body
        assert "### E01" in body
        assert '"source": "fixture"' in body


def test_service_export_rejects_unknown_format(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    result = service.run(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )

    with pytest.raises(ValueError, match="markdown.*json"):
        service.export(result.run_id, format="pdf")


def test_controlled_live_thesis_validation_advances_five_distinct_main_database_chains(
    app_settings,
    repository,
    tmp_path,
) -> None:
    initial = _service(app_settings, repository)
    for _ in range(5):
        initial.run_initial_chain(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-07-24",
                analysts=("market",),
            )
        )
    chains = repository.list_research_chains(instrument="6501.T")
    scenarios = tuple(
        ReviewedLiveThesisScenario(
            scenario=scenario,
            chain_id=chain.id,
            analysis_date=cutoff,
            expected_bounded_result=bounded,
            expected_full_change_conclusion=conclusion,
        )
        for chain, cutoff, scenario, bounded, conclusion in zip(
            chains,
            (date(2026, 7, day) for day in range(25, 30)),
            (
                "quiet_interval",
                "material_event",
                "source_integrity",
                "missing_coverage",
                "threshold_crossing",
            ),
            (
                "no_material_change",
                "source_version_change",
                "source_correction",
                "coverage_incomplete",
                "threshold_crossing",
            ),
            (
                ResearchChangeConclusion.INDETERMINATE,
                ResearchChangeConclusion.MATERIAL_CHANGE,
                ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                ResearchChangeConclusion.INDETERMINATE,
                ResearchChangeConclusion.MATERIAL_CHANGE,
            ),
            strict=True,
        )
    )
    scenario_by_cutoff = {item.analysis_date: item for item in scenarios}

    def gate(baseline, request, *_args):
        scenario = scenario_by_cutoff[request.analysis_date]
        if scenario.scenario != "quiet_interval":
            return IncrementalGateResult(
                escalation_reason=IncrementalEscalationReason(scenario.expected_bounded_result),
                metrics=RunMetrics(tool_calls=1, wall_time_seconds=0.1),
            )
        candidate = baseline.model_copy(
            update={
                "cutoff": request.analysis_date,
                "role": ResearchRevisionRole.UPDATE,
                "execution_strategy": ResearchExecutionStrategy.INCREMENTAL,
                "change_conclusion": ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                "current_state": baseline.current_state.model_copy(
                    update={"cutoff": request.analysis_date}
                ),
                "update_summary": baseline.update_summary.model_copy(
                    update={
                        "summary": "Deterministic gates found no material change.",
                        "baseline_cutoff": baseline.cutoff,
                        "analysis_cutoff": request.analysis_date,
                        "execution_strategy": ResearchExecutionStrategy.INCREMENTAL,
                        "change_conclusion": ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                    }
                ),
            }
        )
        return IncrementalGateResult(
            candidate=candidate,
            transition_coverage=_transition_coverage(
                baseline,
                request.analysis_date,
                complete=True,
            ),
            metrics=RunMetrics(tool_calls=2, wall_time_seconds=0.2),
        )

    def compare(_run_id, _baseline, draft):
        conclusion = scenario_by_cutoff[draft.cutoff].expected_full_change_conclusion
        return draft.model_copy(
            update={
                "role": ResearchRevisionRole.UPDATE,
                "change_conclusion": conclusion,
                "indeterminate_reason": (
                    IndeterminateReason.COVERAGE_INCOMPLETE
                    if conclusion is ResearchChangeConclusion.INDETERMINATE
                    else None
                ),
            }
        )

    service = _service(
        app_settings,
        repository,
        incremental_gate=gate,
        revision_comparator=compare,
    )
    result = validate_live_thesis(
        service,
        scenarios,
        backup_destination=tmp_path / "before-live-validation.db",
        manifest_root=tmp_path / "manifest",
        git_commit="a" * 40,
        environ={"RUN_LIVE_DATA_TESTS": "1", "RUN_LIVE_LLM_TESTS": "1"},
        in_place_database=True,
        verify_source_checkout=lambda: None,
    )

    assert result.passed
    assert len({item.chain_id for item in result.entries}) == 5
    assert all(item.revision_id is not None for item in result.entries)
    quiet_run = repository.get_run(
        next(item.run_id for item in result.entries if item.scenario == "quiet_interval")
    )
    assert quiet_run.research_update_audit.comparison == "inconclusive"


def test_synchronous_chain_update_failure_retains_the_durable_run_id(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    service.run_initial_chain(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    _Graph.error = RuntimeError("fixture Full failure")

    with pytest.raises(ChainUpdateExecutionError) as captured:
        service.run_chain_update(
            chain.id,
            chain.current_revision_id,
            AnalysisRequest(
                ticker="6501.T",
                analysis_date="2026-07-25",
                analysts=("market",),
            ),
        )

    failed = repository.get_run(captured.value.run_id)
    assert failed.status is RunStatus.FAILED
    assert repository.get_research_chain(chain.id).current_revision_id == (
        chain.current_revision_id
    )
