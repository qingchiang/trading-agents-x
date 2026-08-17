from __future__ import annotations

import io
import json
import operator
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock
from typing import Annotated, TypedDict
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import RunRecord
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
        local_name_resolver=lambda _ticker, _date, _config: None,
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
        identity_resolver=lambda _ticker, _date: {
            "company_name": "Toyota Motor Corporation"
        },
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


def test_snapshot_conversion_failure_fails_claimed_run_before_graph(
    app_settings,
    repository,
) -> None:
    service = _service(app_settings, repository)
    queued = service.enqueue(
        AnalysisRequest(
            ticker="NVDA",
            analysis_date="2026-07-24",
            analysts=("market",),
        )
    )
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, queued.id)
        record.request_json = {
            **record.request_json,
            "ticker": "BTC-USD",
            "asset_type": "crypto",
        }
    claimed = repository.claim_run(queued.id, "worker", 30)

    with pytest.raises(ValueError, match="stock|Crypto instruments"):
        service.execute_claimed(claimed, worker_id="worker")

    failed = repository.get_run(queued.id)
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "ValidationError"
    assert "stock" in failed.error_message or "Crypto" in failed.error_message
    assert repository.list_attempts(queued.id)[0].status is RunStatus.FAILED
    events = repository.list_events(queued.id)
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["error_code"] == "ValidationError"
    assert [event.event_type for event in events] == [
        "run.queued",
        "run.started",
        "run.failed",
    ]


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
    assert (
        failed.metrics.node_metrics["analyst.market.serialize.core"].llm_calls
        == 1
    )
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
            assert run_payload["result"]["recoveries"][0]["node"] == (
                "debate.agenda.serialize"
            )
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
        assert payload["result"]["recoveries"][0]["initial_reason_code"] == (
            "non_json_response"
        )
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
