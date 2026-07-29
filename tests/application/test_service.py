from __future__ import annotations

import json
import operator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock
from typing import Annotated

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.repository import RunRepository
from tradingagents.application.runtime import RunCancelled, WorkerShutdown
from tradingagents.application.service import AnalysisService
from tradingagents.dataflows.config import get_config
from tradingagents.graph.research_graph import GraphExecution


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


def _service(
    app_settings,
    repository: RunRepository,
    graph_factory=_Graph,
) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=graph_factory,
        identity_resolver=lambda ticker, _date: {"company_name": ticker},
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
    assert seen == ["run.started", "node.completed", "run.succeeded"]
    events = repository.list_events(result.run_id)
    assert events[0].event_type == "run.queued"
    assert events[-1].event_type == "run.succeeded"
    assert events[2].payload["api_key"] == "[REDACTED]"


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
        assert [artifact.id for artifact in artifacts] == [
            event.payload["artifact_id"]
        ]
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
        claimed.append(
            repository.claim_run(queued.id, f"worker-{index}", 30)
        )

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
    queued = service.enqueue(
        AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    )

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
        checkpoint_config = {
            "configurable": {"thread_id": checkpoint_thread}
        }
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
    assert "run.resumed" in {
        event.event_type for event in repository.list_events(queued.id)
    }
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
        assert saver.get_tuple(
            {"configurable": {"thread_id": checkpoint_thread}}
        ) is None


@pytest.mark.parametrize("format", ("markdown", "json"))
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

    media_type, body = service.export(result.run_id, format=format)

    assert media_type.startswith(
        "text/markdown" if format == "markdown" else "application/json"
    )
    assert result.run_id in body
    assert "Fixture thesis" in body
    if format == "json":
        payload = json.loads(body)
        assert payload["schema_version"] == "1"
        assert payload["run"]["id"] == result.run_id
        assert payload["result"]["evidence"] == payload["evidence"]
        assert payload["evidence"]["items"][0]["source"] == "fixture"
        assert payload["artifacts"][0]["stage"] == "analyst"
        content = payload["artifacts"][0]["content"]
        assert content["sections"][0]["narrative"] == "Fixture report."
    else:
        evidence_ref = result.evidence.items[0].ref
        assert "## Research Process" in body
        assert "### analyst · market · round 0" not in body
        assert "## Reports" in body
        assert "Fixture report." in body
        assert "_No deliberation artifacts were recorded for this run._" in body
        assert "## Warnings" in body
        assert "## Evidence Appendix" in body
        assert f"### `{evidence_ref}`" in body
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
