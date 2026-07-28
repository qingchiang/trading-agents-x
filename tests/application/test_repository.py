from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystClaim,
    AnalystReport,
    EvidenceBundle,
    EvidenceItem,
    ResearchDecision,
    ResearchRating,
    RunStatus,
)
from tradingagents.application.database import RunAttemptRecord
from tradingagents.application.repository import (
    IdempotencyConflictError,
    RunRepository,
)
from tradingagents.application.settings import AppSettings


def _request(ticker: str = "NVDA") -> AnalysisRequest:
    return AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")


def _create(
    repository: RunRepository,
    app_settings: AppSettings,
    ticker: str = "NVDA",
    *,
    idempotency_key: str | None = None,
):
    request = _request(ticker)
    return repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
        idempotency_key=idempotency_key,
    )


def test_idempotent_create_reuses_only_identical_request(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    first, created = _create(
        repository,
        app_settings,
        idempotency_key="browser-submit-1",
    )
    second, created_again = _create(
        repository,
        app_settings,
        idempotency_key="browser-submit-1",
    )

    assert created is True
    assert created_again is False
    assert second.id == first.id

    with pytest.raises(IdempotencyConflictError):
        _create(
            repository,
            app_settings,
            "AAPL",
            idempotency_key="browser-submit-1",
        )


def test_claim_is_atomic_and_expired_lease_is_recovered(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    queued, _ = _create(repository, app_settings)
    barrier = Barrier(2)

    def claim(worker: str):
        barrier.wait(timeout=5)
        return repository.claim_next(worker, 30)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))

    claimed = [item for item in claims if item is not None]
    assert [item.id for item in claimed] == [queued.id]

    repository.fail(queued.id, RuntimeError("provider token=private-value"))
    retry = repository.retry(queued.id)
    checkpoint = repository.checkpoint_thread(queued.id)
    repository.claim_run(retry.id, "worker-a", 0)

    recovered = repository.claim_next("worker-b", 30)

    assert recovered is not None
    assert recovered.id == queued.id
    assert repository.checkpoint_thread(queued.id) == checkpoint
    with repository.sessions() as session:
        attempt = session.scalar(
            select(RunAttemptRecord).where(
                RunAttemptRecord.run_id == queued.id,
                RunAttemptRecord.attempt == 2,
            )
        )
        assert attempt.resume_count == 1


def test_retry_reuses_compatible_checkpoint_across_attempts(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    initial_checkpoint = repository.checkpoint_thread(run.id)

    for expected_attempt in (2, 3):
        repository.claim_run(run.id, "worker", 30)
        repository.fail(run.id, RuntimeError("failed"))
        retried = repository.retry(run.id)

        assert retried.attempt == expected_attempt
        assert repository.checkpoint_thread(run.id) == initial_checkpoint


def test_events_are_monotonic_replayable_and_redacted(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    first = repository.append_event(
        run.id,
        "run.queued",
        payload={"api_key": "private", "message": "token=private"},
    )
    second = repository.append_event(run.id, "run.started", node="market")

    replay = repository.list_events(run.id, after_sequence=1)

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.sequence for event in replay] == [2]
    stored = repository.list_events(run.id)[0].payload
    assert stored["api_key"] == "[REDACTED]"
    assert "private" not in stored["message"]


def test_complete_persists_result_and_resolved_memory(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    evidence_item = EvidenceItem.create(
        source="fixture",
        evidence_type="price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        value=100.0,
        unit="USD",
    )
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(evidence_item,),
    )
    report = AnalystReport(
        analyst="market",
        summary="Momentum is constructive.",
        claims=(
            AnalystClaim(
                text="Price closed at 100.",
                evidence_refs=(evidence_item.ref,),
            ),
        ),
        confidence=0.7,
        evidence_refs=(evidence_item.ref,),
        warnings=("**Historical price** was `partial`.",),
        narrative="Market report.",
    )
    decision = ResearchDecision(
        rating=ResearchRating.OVERWEIGHT,
        confidence=0.7,
        thesis="Constructive evidence outweighs valuation risk.",
        evidence_refs=(evidence_item.ref,),
        catalysts=("Earnings execution",),
        risks=("Multiple compression",),
        invalidation_conditions=("Growth misses expectations",),
        time_horizon="6-12 months",
    )
    result = AnalysisResult(
        run_id=run.id,
        status=RunStatus.SUCCEEDED,
        instrument="NVDA",
        reports={"market": report},
        decision=decision,
    )

    repository.complete(run.id, result, evidence=evidence, benchmark="SPY")
    restored = repository.get_result(run.id)
    pending = repository.pending_outcomes()
    repository.resolve_outcome(
        pending[0]["outcome_id"],
        observation_start=date(2026, 7, 25),
        observation_end=date(2026, 8, 1),
        raw_return=0.08,
        alpha_return=0.03,
        reflection="The thesis worked because earnings accelerated.",
    )

    assert restored.status is RunStatus.SUCCEEDED
    assert restored.decision == decision
    assert isinstance(restored.reports["market"], AnalystReport)
    assert restored.warnings[0].message == "Historical price was partial."
    context = repository.memory_context("NVDA", "stock")
    assert "The thesis worked" in context
    assert "NVDA" in context


def test_rerun_links_new_run_and_backup_is_consistent(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source, _ = _create(repository, app_settings)
    rerun = repository.rerun(source.id)
    backup = repository.backup(tmp_path / "backup" / "snapshot.db")

    assert rerun.id != source.id
    assert rerun.parent_run_id == source.id
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (2,)

    with pytest.raises(ValueError, match="must differ"):
        repository.backup(app_settings.database_path)
