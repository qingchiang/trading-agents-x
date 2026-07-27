from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock

import pytest

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystReport,
    EvidenceBundle,
    EvidenceItem,
    ResearchDecision,
    ResearchRating,
    RunStatus,
)
from tradingagents.application.repository import RunRepository
from tradingagents.application.runtime import RunCancelled
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
    report = AnalystReport(
        analyst="market",
        summary="Fixture summary.",
        confidence=0.8,
        evidence_refs=(item.ref,),
        narrative="Fixture report.",
    )
    decision = ResearchDecision(
        rating=ResearchRating.HOLD,
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(item.ref,),
        time_horizon="6-12 months",
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


@pytest.fixture(autouse=True)
def _reset_graph():
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None
    yield
    _Graph.barrier = None
    _Graph.observed = []
    _Graph.error = None


def _service(app_settings, repository: RunRepository) -> AnalysisService:
    return AnalysisService(
        app_settings,
        repository=repository,
        llm_factory=lambda *_args, **_kwargs: (object(), object()),
        graph_factory=_Graph,
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
    assert seen == ["run.started", "node.completed", "run.succeeded"]
    events = repository.list_events(result.run_id)
    assert events[0].event_type == "run.queued"
    assert events[-1].event_type == "run.succeeded"
    assert events[2].payload["api_key"] == "[REDACTED]"


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
