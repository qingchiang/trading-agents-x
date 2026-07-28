from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystClaim,
    AnalystReport,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    ResearchDecision,
    ResearchRating,
    RunArchiveState,
    RunStatus,
)
from tradingagents.application.database import RunAttemptRecord, RunRecord
from tradingagents.application.maintenance import ArchiveMaintenance
from tradingagents.application.repository import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunNotFoundError,
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


def test_archive_restore_filters_are_atomic_and_idempotent(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    terminal, _ = _create(repository, app_settings, "NVDA")
    queued, _ = _create(repository, app_settings, "AAPL")
    repository.request_cancel(terminal.id)

    with pytest.raises(
        InvalidRunTransitionError,
        match="only terminal runs",
    ):
        repository.archive_runs((terminal.id, queued.id))

    assert repository.get_run(terminal.id).archived_at is None
    archived, changed = repository.archive_runs((terminal.id,))
    repeated, changed_again = repository.archive_runs((terminal.id,))

    assert changed == 1
    assert changed_again == 0
    assert archived[0].archived_at is not None
    assert repeated[0].archived_at == archived[0].archived_at
    assert repository.list_runs().items == (repository.get_run(queued.id),)
    archived_page = repository.list_runs(
        archive_state=RunArchiveState.ARCHIVED,
        q="nv",
    )
    assert archived_page.total == 1
    assert archived_page.items[0].id == terminal.id
    all_page = repository.list_runs(
        archive_state=RunArchiveState.ALL,
        limit=1,
        offset=1,
    )
    assert all_page.total == 2
    assert len(all_page.items) == 1

    restored, restored_changed = repository.restore_runs((terminal.id,))
    _, restored_again = repository.restore_runs((terminal.id,))

    assert restored_changed == 1
    assert restored_again == 0
    assert restored[0].archived_at is None
    assert repository.list_runs().total == 2


def test_recent_instruments_are_deduplicated_and_exclude_archives(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    older, _ = _create(repository, app_settings, "NVDA")
    archived, _ = _create(repository, app_settings, "AAPL")
    latest, _ = _create(repository, app_settings, "NVDA")
    repository.set_instrument_name(older.id, "NVIDIA Corporation")
    repository.set_instrument_name(archived.id, "Apple")
    repository.set_instrument_name(latest.id, "NVIDIA")
    with repository.sessions.begin() as session:
        session.get(RunRecord, older.id).created_at = datetime(2026, 7, 1)
        session.get(RunRecord, archived.id).created_at = datetime(2026, 7, 2)
        session.get(RunRecord, latest.id).created_at = datetime(2026, 7, 3)
    repository.request_cancel(archived.id)
    repository.archive_runs((archived.id,))

    recent = repository.recent_instruments()

    assert [(item.ticker, item.instrument_name) for item in recent] == [
        ("NVDA", "NVIDIA")
    ]
    assert recent[0].last_used_at == datetime(
        2026,
        7,
        3,
        tzinfo=timezone.utc,
    )
    assert repository.list_runs(q="nvidia").total == 2


def test_artifacts_are_typed_retained_and_idempotent_across_retries(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker-1", 30)
    report = AnalystReport(
        analyst="market",
        summary="Fixture summary.",
        confidence=0.8,
        narrative="Fixture narrative must not enter event payloads.",
    )
    draft = ResearchArtifactDraft(
        node="analyst.market",
        stage="analyst",
        role="market",
        generation_method=ArtifactGenerationMethod.TOOL_CALL,
        content=report,
    )

    first, first_event = repository.append_artifact(run.id, draft)
    duplicate, duplicate_event = repository.append_artifact(run.id, draft)
    repository.fail(run.id, RuntimeError("retry fixture"))
    repository.retry(run.id)
    repository.claim_run(run.id, "worker-2", 30)
    retried, retried_event = repository.append_artifact(run.id, draft)
    changed, changed_event = repository.append_artifact(
        run.id,
        draft.model_copy(
            update={
                "content": report.model_copy(
                    update={"summary": "Recomputed summary."}
                )
            }
        ),
    )

    assert first == duplicate == retried
    assert first.attempt == 1
    assert duplicate_event is None
    assert retried_event is None
    assert changed.attempt == 2
    assert first_event is not None
    assert changed_event is not None
    assert [artifact.id for artifact in repository.list_artifacts(run.id)] == [
        first.id,
        changed.id,
    ]
    events = repository.list_events(run.id)
    assert [event.event_type for event in events] == [
        "artifact.created",
        "artifact.created",
    ]
    assert events[0].payload == {
        "artifact_id": first.id,
        "attempt": 1,
        "stage": "analyst",
        "role": "market",
        "round": 0,
        "schema_version": "1",
        "generation_method": "tool_call",
        "content_type": "analyst_report",
    }
    assert "Fixture narrative" not in str(events[0].payload)


def test_recovered_artifact_surfaces_a_top_level_result_warning(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="review.bear",
            stage="perspective",
            role="bear",
            generation_method=ArtifactGenerationMethod.RAW_JSON_RECOVERED,
            content=ResearchDecision(
                rating=ResearchRating.HOLD,
                confidence=0.5,
                thesis="Fixture recovered thesis.",
                evidence_refs=(),
                risks=("Fixture risk.",),
                invalidation_conditions=("Fixture invalidation.",),
                time_horizon="6-12 months",
            ),
        ),
    )

    result = repository.get_result(run.id)

    assert result.warnings[0].code == "structured_output.recovered"
    assert "raw_json_recovered" in result.warnings[0].message


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
    repository.archive_runs((run.id,))
    assert repository.pending_outcomes() == []
    assert repository.memory_entries() == []
    repository.restore_runs((run.id,))
    assert repository.pending_outcomes()[0]["outcome_id"] == (
        pending[0]["outcome_id"]
    )
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
    assert restored.evidence == evidence
    assert isinstance(restored.reports["market"], AnalystReport)
    assert restored.warnings[0].message == "Historical price was partial."
    context = repository.memory_context("NVDA", "stock")
    assert len(context.items) == 1
    assert context.items[0].ticker == "NVDA"
    assert "The thesis worked" in context.items[0].reflection
    repository.archive_runs((run.id,))
    assert repository.memory_context("NVDA", "stock").items == ()
    assert repository.memory_entries() == []
    repository.restore_runs((run.id,))
    assert repository.memory_context("NVDA", "stock").items[0].run_id == run.id


def test_research_template_requires_terminal_source_and_backup_is_consistent(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source, _ = _create(repository, app_settings)
    with pytest.raises(
        InvalidRunTransitionError,
        match="source run must be terminal",
    ):
        repository.create_run(
            _request("AAPL"),
            app_settings.resolve_run(_request("AAPL")).snapshot(),
            source_run_id=source.id,
        )
    repository.request_cancel(source.id)
    template_request = _request("AAPL")
    created, _ = repository.create_run(
        template_request,
        app_settings.resolve_run(template_request).snapshot(),
        source_run_id=source.id,
    )
    backup = repository.backup(tmp_path / "backup" / "snapshot.db")

    assert created.id != source.id
    assert created.source_run_id == source.id
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (2,)

    with pytest.raises(ValueError, match="must differ"):
        repository.backup(app_settings.database_path)


def test_research_template_rejects_missing_source_and_idempotency_mismatch(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    request = _request("NVDA")
    with pytest.raises(RunNotFoundError):
        repository.create_run(
            request,
            app_settings.resolve_run(request).snapshot(),
            source_run_id="00000000-0000-0000-0000-000000000000",
        )

    source, _ = _create(repository, app_settings, "AAPL")
    repository.request_cancel(source.id)
    repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
        source_run_id=source.id,
        idempotency_key="template-submit",
    )
    with pytest.raises(IdempotencyConflictError):
        repository.create_run(
            request,
            app_settings.resolve_run(request).snapshot(),
            idempotency_key="template-submit",
        )


def test_research_template_and_source_purge_are_race_safe(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    source, _ = _create(repository, app_settings, "NVDA")
    repository.request_cancel(source.id)
    repository.archive_runs((source.id,))
    with repository.sessions.begin() as session:
        session.get(RunRecord, source.id).archived_at = datetime(2026, 7, 1)
    request = _request("AAPL")
    barrier = Barrier(2)

    def create_from_source():
        barrier.wait(timeout=5)
        try:
            return repository.create_run(
                request,
                app_settings.resolve_run(request).snapshot(),
                source_run_id=source.id,
            )[0]
        except RunNotFoundError:
            return None

    def purge_source():
        barrier.wait(timeout=5)
        return ArchiveMaintenance(
            app_settings,
            repository,
            utc_clock=lambda: datetime(
                2026,
                9,
                1,
                tzinfo=timezone.utc,
            ),
        ).run_once()

    with ThreadPoolExecutor(max_workers=2) as executor:
        created_future = executor.submit(create_from_source)
        purge_future = executor.submit(purge_source)
        created = created_future.result(timeout=10)
        assert purge_future.result(timeout=10) == 1

    with pytest.raises(RunNotFoundError):
        repository.get_run(source.id)
    if created is not None:
        assert repository.get_run(created.id).source_run_id is None


def test_reports_use_canonical_order_across_result_and_repository(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "fixture-worker", 30)
    evidence = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(),
    )
    result = AnalysisResult(
        run_id=run.id,
        status=RunStatus.SUCCEEDED,
        instrument="NVDA",
        reports={
            "social": "Social report.",
            "news": "News report.",
            "market": "Market report.",
            "fundamentals": "Fundamentals report.",
            "legacy": "Legacy report.",
        },
        decision=None,
    )

    assert list(result.reports) == [
        "fundamentals",
        "market",
        "news",
        "social",
        "legacy",
    ]

    repository.complete(run.id, result, evidence=evidence, benchmark="SPY")
    restored = repository.get_result(run.id)

    assert list(restored.reports) == [
        "fundamentals",
        "market",
        "news",
        "social",
        "legacy",
    ]
