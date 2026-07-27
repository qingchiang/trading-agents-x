from __future__ import annotations

from tradingagents.application.contracts import AnalysisRequest, RunStatus
from tradingagents.application.worker import AnalysisWorker


class _Service:
    def __init__(self, repository):
        self.repository = repository
        self.executed = []

    def execute_claimed(self, run, *, worker_id):
        self.executed.append((run.id, worker_id, run.status))


class _Settlement:
    def __init__(self):
        self.calls = []

    def settle_once(self, *, limit):
        self.calls.append(limit)


def test_worker_prioritizes_queued_analysis_over_settlement(
    app_settings,
    repository,
) -> None:
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    queued, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    service = _Service(repository)
    settlement = _Settlement()
    worker = AnalysisWorker(
        app_settings,
        service=service,
        settlement=settlement,
        worker_id="fixture-worker",
    )

    assert worker.run_once() is True
    assert service.executed == [
        (queued.id, "fixture-worker", RunStatus.RUNNING)
    ]
    assert settlement.calls == []


def test_idle_worker_runs_low_priority_settlement(
    app_settings,
    repository,
) -> None:
    service = _Service(repository)
    settlement = _Settlement()
    worker = AnalysisWorker(
        app_settings,
        service=service,
        settlement=settlement,
        worker_id="fixture-worker",
    )

    assert worker.run_once() is False
    assert settlement.calls == [10]
