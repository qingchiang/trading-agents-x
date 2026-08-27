from __future__ import annotations

from tradingagents.application.contracts import AnalysisRequest, RunStatus
from tradingagents.application.worker import AnalysisWorker


class _Service:
    def __init__(self, repository):
        self.repository = repository
        self.executed = []

    def execute_claimed(self, run, *, worker_id, shutdown_requested):
        assert shutdown_requested() is False
        self.executed.append((run.id, worker_id, run.status))


class _Maintenance:
    def __init__(self, *, failures=0):
        self.calls = 0
        self.failures = failures

    def run_once(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("fixture maintenance failure")
        return 0


def test_worker_prioritizes_queued_analysis(
    app_settings,
    repository,
) -> None:
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    queued, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    service = _Service(repository)
    worker = AnalysisWorker(
        app_settings,
        service=service,
        maintenance=_Maintenance(),
        worker_id="fixture-worker",
    )

    assert worker.run_once() is True
    assert service.executed == [
        (queued.id, "fixture-worker", RunStatus.RUNNING)
    ]


def test_busy_worker_runs_maintenance_immediately_and_every_24_hours(
    app_settings,
    repository,
) -> None:
    for ticker in ("NVDA", "AAPL", "MSFT"):
        request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")
        repository.create_run(
            request,
            app_settings.resolve_run(request).snapshot(),
        )
    clock = [100.0]
    maintenance = _Maintenance()
    worker = AnalysisWorker(
        app_settings,
        service=_Service(repository),
        maintenance=maintenance,
        monotonic_clock=lambda: clock[0],
        worker_id="fixture-worker",
    )

    assert worker.run_once() is True
    clock[0] += 86_399
    assert worker.run_once() is True
    assert maintenance.calls == 1
    clock[0] += 1
    assert worker.run_once() is True
    assert maintenance.calls == 2


def test_maintenance_failure_retries_after_one_hour_without_blocking_work(
    app_settings,
    repository,
    caplog,
) -> None:
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    clock = [50.0]
    maintenance = _Maintenance(failures=1)
    service = _Service(repository)
    worker = AnalysisWorker(
        app_settings,
        service=service,
        maintenance=maintenance,
        monotonic_clock=lambda: clock[0],
        worker_id="fixture-worker",
    )

    assert worker.run_once() is True
    assert len(service.executed) == 1
    assert "RuntimeError" in caplog.text
    assert "fixture maintenance failure" not in caplog.text
    clock[0] += 3_599
    worker.run_once()
    assert maintenance.calls == 1
    clock[0] += 1
    worker.run_once()
    assert maintenance.calls == 2
