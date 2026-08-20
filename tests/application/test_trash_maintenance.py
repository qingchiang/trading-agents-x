from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from tests.factories import (
    analyst_report,
    research_decision,
    seed_legacy_outcome,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    OutcomeRecord,
    ReflectionRecord,
    RunArtifactRecord,
    RunAttemptRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
)
from tradingagents.application.maintenance import TrashMaintenance
from tradingagents.application.repository import RunNotFoundError


def _cancel_and_trash(repository, app_settings, ticker: str):
    request = AnalysisRequest(ticker=ticker, analysis_date="2026-07-24")
    run, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    repository.request_cancel(run.id)
    repository.trash_runs((run.id,))
    return run


def _set_trashed_at(repository, run_id: str, value: datetime) -> None:
    with repository.sessions.begin() as session:
        record = session.get(RunRecord, run_id)
        assert record is not None
        record.trashed_at = value.replace(tzinfo=None)


def _insert_checkpoint(app_settings, thread_id: str) -> None:
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        saver.conn.execute(
            """
            INSERT INTO checkpoints (
                thread_id, checkpoint_ns, checkpoint_id
            ) VALUES (?, '', 'checkpoint-1')
            """,
            (thread_id,),
        )
        saver.conn.execute(
            """
            INSERT INTO writes (
                thread_id, checkpoint_ns, checkpoint_id,
                task_id, idx, channel
            ) VALUES (?, '', 'checkpoint-1', 'task-1', 0, 'fixture')
            """,
            (thread_id,),
        )
        saver.conn.commit()


def _complete_trashed_run(repository, app_settings):
    request = AnalysisRequest(ticker="NVDA", analysis_date="2026-07-24")
    run, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    repository.claim_run(run.id, "fixture-worker", 30)
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
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        evidence_ref=evidence_item.ref,
        narrative="Fixture report.",
    )
    repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="analyst.market",
            stage="analyst",
            role="market",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=report,
        ),
    )
    repository.seal_evidence(run.id, evidence)
    decision = research_decision(
        confidence=0.6,
        thesis="Fixture thesis.",
        evidence_refs=(evidence_item.ref,),
        risks=("Fixture risk.",),
        invalidation_conditions=("Fixture invalidation.",),
        time_horizon="6-12 months",
    )
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument="NVDA",
            reports={"market": report},
            decision=decision,
            evidence=evidence,
        ),
        evidence=evidence,
    )
    seed_legacy_outcome(
        repository,
        run.id,
        next_check_at=datetime(2026, 9, 1),
    )
    pending = repository.pending_outcomes(
        due_at=datetime(2026, 9, 1, tzinfo=UTC)
    )[0]
    repository.resolve_outcome(
        pending["outcome_id"],
        observation_start=date(2026, 7, 25),
        observation_end=date(2026, 8, 1),
        raw_return=0.05,
        alpha_return=0.01,
        reflection="Fixture reflection.",
    )
    repository.append_event(run.id, "fixture.completed")
    repository.trash_runs((run.id,))
    return run


def test_trash_maintenance_purges_owned_data_and_detaches_child_runs(
    repository,
    app_settings,
) -> None:
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    run = _complete_trashed_run(repository, app_settings)
    child_request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-25",
    )
    child, _ = repository.create_run(
        child_request,
        app_settings.resolve_run(child_request).snapshot(),
        source_run_id=run.id,
    )
    checkpoint_thread = repository.checkpoint_thread(run.id)
    _insert_checkpoint(app_settings, checkpoint_thread)
    _set_trashed_at(repository, run.id, now - timedelta(days=31))

    purged = TrashMaintenance(
        app_settings,
        repository,
        utc_clock=lambda: now,
        batch_size=1,
    ).run_once()

    assert purged == 1
    with pytest.raises(RunNotFoundError):
        repository.get_run(run.id)
    assert repository.get_run(child.id).source_run_id is None
    with repository.sessions() as session:
        for model in (
            RunAttemptRecord,
            RunEventRecord,
            RunArtifactRecord,
            RunEvidenceRecord,
            DecisionRecord,
        ):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.run_id == run.id)
                )
                == 0
            )
        assert (
            session.scalar(select(func.count()).select_from(OutcomeRecord)) == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(ReflectionRecord))
            == 0
        )
    with repository.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?",
                (checkpoint_thread,),
            ).scalar_one()
            == 0
        )
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM writes WHERE thread_id = ?",
                (checkpoint_thread,),
            ).scalar_one()
            == 0
        )


def test_trash_maintenance_honors_cutoff_restore_and_disabled_retention(
    repository,
    app_settings,
) -> None:
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    boundary = _cancel_and_trash(repository, app_settings, "NVDA")
    newer = _cancel_and_trash(repository, app_settings, "AAPL")
    restored = _cancel_and_trash(repository, app_settings, "MSFT")
    _set_trashed_at(repository, boundary.id, now - timedelta(days=30))
    _set_trashed_at(
        repository,
        newer.id,
        now - timedelta(days=30) + timedelta(microseconds=1),
    )
    _set_trashed_at(repository, restored.id, now - timedelta(days=31))
    repository.restore_runs((restored.id,))

    purged = TrashMaintenance(
        app_settings,
        repository,
        utc_clock=lambda: now,
    ).run_once()

    assert purged == 1
    with pytest.raises(RunNotFoundError):
        repository.get_run(boundary.id)
    assert repository.get_run(newer.id).trashed_at is not None
    assert repository.get_run(restored.id).trashed_at is None

    disabled = app_settings.model_copy(
        update={"trash_retention_days": 0}
    )
    _set_trashed_at(repository, newer.id, now - timedelta(days=90))
    assert (
        TrashMaintenance(
            disabled,
            repository,
            utc_clock=lambda: now,
        ).run_once()
        == 0
    )
    assert repository.get_run(newer.id).trashed_at is not None


def test_checkpoint_delete_failure_rolls_back_application_deletion(
    repository,
    app_settings,
) -> None:
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    run = _cancel_and_trash(repository, app_settings, "NVDA")
    checkpoint_thread = repository.checkpoint_thread(run.id)
    _insert_checkpoint(app_settings, checkpoint_thread)
    _set_trashed_at(repository, run.id, now - timedelta(days=31))
    with repository.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER block_checkpoint_purge
            BEFORE DELETE ON writes
            BEGIN
                SELECT RAISE(ABORT, 'fixture checkpoint failure');
            END
            """
        )

    with pytest.raises(Exception, match="fixture checkpoint failure"):
        TrashMaintenance(
            app_settings,
            repository,
            utc_clock=lambda: now,
        ).run_once()

    assert repository.get_run(run.id).trashed_at is not None
    with repository.engine.begin() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM writes WHERE thread_id = ?",
                (checkpoint_thread,),
            ).scalar_one()
            == 1
        )
        connection.exec_driver_sql("DROP TRIGGER block_checkpoint_purge")


def test_concurrent_trash_maintenance_is_idempotent(
    repository,
    app_settings,
) -> None:
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    run = _cancel_and_trash(repository, app_settings, "NVDA")
    _set_trashed_at(repository, run.id, now - timedelta(days=31))

    def purge() -> int:
        return TrashMaintenance(
            app_settings,
            repository,
            utc_clock=lambda: now,
        ).run_once()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: purge(), range(2)))

    assert sum(results) == 1
    with pytest.raises(RunNotFoundError):
        repository.get_run(run.id)
