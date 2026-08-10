from __future__ import annotations

import io
import json
import operator
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier, Lock
from typing import Annotated
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from typing_extensions import TypedDict

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunMetrics,
    RunStatus,
)
from tradingagents.application.live_thesis_validation import (
    ReviewedLiveThesisScenario,
    validate_live_thesis,
)
from tradingagents.application.llms import RunLLMs
from tradingagents.application.outcomes import OutcomeObservation, OutcomeSettlement
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
    SourceRecordSnapshotItem,
    SourceWatermarkSnapshot,
    assemble_full_revision,
    validate_experimental_nmc_candidate,
)
from tradingagents.application.runtime import RunCancelled, WorkerShutdown
from tradingagents.application.service import AnalysisService, ChainUpdateExecutionError
from tradingagents.dataflows.config import get_config
from tradingagents.graph.research_graph import GraphExecution
from tradingagents.provenance import SourceWatermark, attach_source_watermarks


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
    yield
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    _ResumableGraph.first_calls = 0
    _ResumableGraph.second_calls = 0
    _ResumableGraph.fail_second_once = True


def _eligible_state_assembler(request, execution):
    draft = assemble_full_revision(request, execution)
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
            )
        }
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
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
        local_name_resolver=lambda _ticker, _date, _config: None,
        **kwargs,
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
    outcome = repository.pending_outcomes(
        due_at=datetime(2100, 1, 1, tzinfo=timezone.utc)
    )[0]
    repository.persist_outcome_observation(
        outcome["outcome_id"],
        observation=OutcomeObservation(
            raw_return=0.03,
            alpha_return=0.01,
            holding_intervals=5,
            start_date=date(2026, 7, 25),
            end_date=date(2026, 8, 1),
        ),
        observed_at=datetime(2026, 8, 1, 20, tzinfo=timezone.utc),
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
            reflection="Method lesson: Use a bounded methodological check.",
            generated_at=datetime(2026, 8, 1, 20, 1, tzinfo=timezone.utc),
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
            datetime(2100, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, 0, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, 0, 2, tzinfo=timezone.utc),
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
        lambda **_kwargs: "Method lesson: Use a bounded methodological check.",
    )

    stats = settlement.settle_once()

    feedback = repository.memory_entries(ticker="NVDA")[0]["outcome_feedback"]
    assert stats == {"checked": 1, "resolved": 1, "pending": 0, "failed": 0}
    assert feedback["status"] == "eligible"
    assert feedback["qualification_policy_version"] == (
        "outcome_feedback_qualification.v1"
    )
    assert repository.get_run(result.run_id).status is RunStatus.SUCCEEDED
    assert repository.get_research_chain(chain.id).current_revision == revision_before


def test_full_update_is_idempotent_for_current_head_and_advances_atomically(
    app_settings,
    repository,
    tmp_path,
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
    ("mode", "whitelist", "ticker", "expected_strategy"),
    [
        ("off", ("6501.T",), "6501.T", "full"),
        ("shadow", ("6501.T",), "6501.T", "incremental"),
        ("experimental", ("6501.T",), "6501.T", "incremental"),
        ("experimental", ("7203.T",), "6501.T", "full"),
        ("experimental", ("6501.T",), "NVDA", "full"),
    ],
)
def test_chain_update_strategy_respects_mode_and_japanese_whitelist(
    app_settings,
    repository,
    mode,
    whitelist,
    ticker,
    expected_strategy,
) -> None:
    configured = app_settings.model_copy(
        update={
            "research_update_mode": mode,
            "experimental_nmc_jp_whitelist": whitelist,
        }
    )
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
    assert queued.config_snapshot["experimental_nmc_jp_whitelist"] == list(whitelist)


def test_ineligible_head_rejects_explicit_incremental_but_allows_full(
    app_settings,
    repository,
) -> None:
    service = _service(
        app_settings,
        repository,
        state_assembler=assemble_full_revision,
    )
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
    )
    chain = repository.list_research_chains(instrument="6501.T")[0]
    assert chain.next_update_policy == "full_required"
    assert chain.next_update_reason == "coverage_incomplete"
    request = AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",))

    with pytest.raises(InvalidResearchBaselineError, match="does not allow Incremental Execution"):
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
        state_assembler=assemble_full_revision,
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

    advanced = repository.get_research_chain(chain.id)
    revision = advanced.current_revision
    assert result.status is RunStatus.SUCCEEDED
    assert revision.role is ResearchRevisionRole.UPDATE
    assert revision.execution_strategy is ResearchExecutionStrategy.FULL
    assert revision.change_conclusion is ResearchChangeConclusion.INDETERMINATE
    assert revision.indeterminate_reason.value == "coverage_incomplete"
    assert advanced.next_update_policy == "full_required"
    assert advanced.next_update_reason == "indeterminate_head"
    assert revision.research_update_audit.comparison == "not_applicable"

    next_run = service.enqueue_chain_update(
        advanced.id,
        revision.id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-26", analysts=("market",)),
    )
    assert next_run.research_execution_strategy == "full"


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
    assert revision.metrics.tool_calls == result.metrics.tool_calls == 2
    events = repository.list_events(queued.id)
    assert [event.event_type for event in events] == [
        "run.queued",
        "run.started",
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
    inherited_snapshot = baseline.evidence_snapshot.model_copy(
        update={
            "bundle": baseline.evidence_snapshot.bundle.model_copy(
                update={"analysis_date": cutoff}
            ),
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
        }
    )
    candidate = ResearchRevisionDraft(
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
    assert validate_experimental_nmc_candidate(baseline, candidate) is None

    class FullAnalysisMustNotRun:
        def __init__(self, **_kwargs):
            raise AssertionError("Full Analysis must not be constructed for experimental NMC")

    experimental_settings = app_settings.model_copy(
        update={
            "research_update_mode": "experimental",
            "experimental_nmc_jp_whitelist": ("6501.T",),
        }
    )
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
    assert revision.metrics.tool_calls == 5
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
    )
    service.run_initial_chain(
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-24", analysts=("market",))
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
    assert audit.bounded_metrics.tool_calls == 1


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
    payload = attach_source_watermarks(
        "No deterministic material change was found.",
        *(
            SourceWatermark(
                source=source,
                scanned_start="2026-07-01",
                scanned_end="2026-07-25",
                status="complete",
            )
            for source in (
                "EDINET",
                "TDnet",
                "Google News",
                "J-Quants adjusted OHLCV",
            )
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
    assert llm_factory_calls == 2
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
    )
    queued = shadow_service.enqueue_chain_update(
        chain.id,
        chain.current_revision_id,
        AnalysisRequest(ticker="6501.T", analysis_date="2026-07-25", analysts=("market",)),
    )
    payload = attach_source_watermarks(
        "No bounded source changes were returned.",
        *(
            SourceWatermark(
                source=source,
                scanned_start="2026-07-24",
                scanned_end="2026-07-25",
                status="complete",
            )
            for source in (
                "EDINET",
                "TDnet",
                "Google News",
                "J-Quants fundamentals",
                "J-Quants adjusted OHLCV",
            )
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
    assert audit.escalation_reason is None
    assert result.status is RunStatus.CANCELLED, audit
    assert {item.source for item in audit.checked_windows} == {
        "EDINET",
        "TDnet",
        "Google News",
        "J-Quants fundamentals",
        "J-Quants adjusted OHLCV",
    }
    assert audit.bounded_metrics.tool_calls == 1
    assert run.metrics.tool_calls == 1
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
    assert run.metrics.tool_calls == 2
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

    experimental_settings = app_settings.model_copy(
        update={
            "research_update_mode": "experimental",
            "experimental_nmc_jp_whitelist": ("6501.T",),
        }
    )
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
    assert result.metrics.tool_calls == 1
    assert "research.full_escalation_started" in {
        event.event_type for event in repository.list_events(queued.id)
    }


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
    repository.purge_expired_trash(cutoff=datetime.now(timezone.utc) + timedelta(days=1))

    revision = repository.get_research_revision(revision_id)
    assert revision.producing_run_id is None
    assert revision.current_state.opinion.thesis == "Fixture thesis."
    assert revision.evidence_snapshot.bundle.items[0].content == "Fixture evidence."


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
