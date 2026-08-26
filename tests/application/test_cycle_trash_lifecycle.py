from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    EvidenceItem,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    ResearchNodeRecord,
    RunAttemptRecord,
    RunEvidenceRecord,
    RunRecord,
)
from tradingagents.application.errors import IncrementalRequestConflictError
from tradingagents.application.maintenance import TrashMaintenance
from tradingagents.application.repository import (
    InvalidRunTransitionError,
    RunNotFoundError,
)


def _commit_node(
    repository,
    app_settings,
    *,
    analysis_date: date,
    baseline_id: str | None = None,
    make_primary: bool | None = None,
):
    research_kind = "incremental" if baseline_id else "full"
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=analysis_date,
        research_kind=research_kind,
        full_baseline_run_id=baseline_id,
        make_primary=make_primary,
    )
    run, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
        research_schema_version="1",
        information_cutoff_at=datetime.combine(
            analysis_date, datetime.max.time(), UTC
        ),
        method_snapshot={"schema_version": "1", "llm_provider": "fixture"},
        research_kind=research_kind,
        full_baseline_run_id=baseline_id,
        incremental_input_fingerprint=(
            f"fingerprint-{analysis_date.isoformat()}" if baseline_id else None
        ),
    )
    repository.claim_run(run.id, "fixture", 30)
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fixture",
        requested_date=analysis_date,
        effective_date=analysis_date,
        content=run.id,
    )
    evidence = EvidenceBundle(
        instrument="NVDA", analysis_date=analysis_date, items=(item,)
    )
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument="NVDA",
            reports={},
            decision=research_decision(evidence_refs=(item.ref,)),
            evidence=evidence,
        ),
        evidence=evidence,
    )
    if baseline_id:
        with repository.sessions.begin() as session:
            session.add(
                ResearchNodeRecord(
                    run_id=run.id,
                    research_kind="incremental",
                    full_baseline_run_id=baseline_id,
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                    incremental_products_json=None,
                )
            )
    return repository.get_run(run.id)


def _warning_products() -> dict[str, object]:
    return {
        "collection_summary": {
            "version": "1",
            "market": "united_states",
            "domains": [
                {
                    "domain": "news",
                    "state": "empty",
                    "sources": [
                        {
                            "source": "fixture",
                            "retrieved_at": "2026-07-26T20:00:00Z",
                        }
                    ],
                }
            ],
        },
        "research_availability": {
            "version": "1",
            "domains": [{"domain": "news", "status": "missing"}],
        },
        "information_advancement": {
            "advanced": True,
            "reasons": ["completed_stock_session"],
            "observation_ids": [],
        },
        "performance": {
            "stock": {"status": "unavailable", "reason": "fixture"},
            "benchmarks": [],
        },
        "reassessment": {
            "entries": [
                {
                    "component_id": "thesis",
                    "disposition": "unresolved",
                    "reason": "fixture",
                    "evidence_refs": [],
                }
            ]
        },
        "full_research_required_reasons": [
            {
                "code": "attribution.unreliable",
                "message": "Fixture warning.",
                "origin": "semantic",
                "evidence_refs": [],
            }
        ],
    }


def test_independent_incremental_trash_updates_the_active_cycle(repository, app_settings):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    first = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    head = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 26),
        baseline_id=full.id,
    )
    with repository.sessions.begin() as session:
        session.get(ResearchNodeRecord, head.id).incremental_products_json = (
            _warning_products()
        )

    warned = repository.get_timeline("NVDA")
    assert warned.timeline_warning is True
    assert next(node for node in warned.nodes if node.id == head.id).cycle_warning

    result = repository.trash_runs_detailed((head.id,))

    assert result.changed == 1
    assert result.impacts[0].cycle_id == full.id
    assert result.impacts[0].affected_run_ids == (head.id,)
    active = repository.get_timeline("NVDA")
    assert tuple(node.id for node in active.nodes) == (full.id, first.id)
    new_head = next(node for node in active.nodes if node.id == first.id)
    assert new_head.is_cycle_head
    assert new_head.is_primary
    assert active.timeline_warning is False
    retained = repository.get_timeline("NVDA", trash_state="all")
    assert tuple(node.id for node in retained.nodes) == (full.id, first.id, head.id)
    assert next(node for node in retained.nodes if node.id == head.id).is_active is False
    trashed_only = repository.get_timeline("NVDA", trash_state="trashed")
    assert tuple(node.id for node in trashed_only.nodes) == (head.id,)


def test_full_trash_cascades_only_active_children_and_records_them(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    independently_trashed = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    cascaded = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 26),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((independently_trashed.id,))

    result = repository.trash_runs_detailed((full.id,))

    assert result.changed == 2
    assert result.impacts[0].affected_run_ids == (full.id, cascaded.id)
    assert result.impacts[0].cascade_moved_run_ids == (cascaded.id,)
    retained = repository.get_timeline("NVDA", trash_state="all")
    by_id = {node.id: node for node in retained.nodes}
    assert by_id[cascaded.id].trash_cascade_full_run_id == full.id
    assert by_id[independently_trashed.id].trash_cascade_full_run_id is None
    assert repository.get_timeline("NVDA").nodes == ()


def test_primary_full_trash_requires_an_explicit_active_replacement(
    repository, app_settings
):
    primary = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    replacement = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        make_primary=False,
    )

    with pytest.raises(
        InvalidRunTransitionError,
        match="explicit replacement",
    ):
        repository.trash_runs_detailed((primary.id,))

    assert repository.get_run(primary.id).trashed_at is None
    result = repository.trash_runs_detailed(
        (primary.id,),
        primary_replacements={primary.id: replacement.id},
    )

    assert result.impacts[0].replacement_primary_cycle_id == replacement.id
    assert repository.get_timeline("NVDA").primary_cycle_id == replacement.id


def test_full_restore_preserves_independent_trash_and_rejects_slot_conflicts(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    independent = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    cascaded = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 26),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((independent.id,))
    repository.trash_runs_detailed((full.id,))

    with pytest.raises(InvalidRunTransitionError, match="Full remains in Trash"):
        repository.restore_runs_detailed((cascaded.id,))

    restored = repository.restore_runs_detailed((full.id,))

    assert restored.changed == 2
    assert restored.impacts[0].affected_run_ids == (full.id, cascaded.id)
    assert repository.get_run(independent.id).trashed_at is not None
    assert repository.get_run(cascaded.id).trashed_at is None
    assert repository.get_timeline("NVDA").primary_cycle_id == full.id

    repository.trash_runs_detailed((cascaded.id,))
    replacement = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 26),
        baseline_id=full.id,
    )
    with pytest.raises(InvalidRunTransitionError, match="active slot"):
        repository.restore_runs_detailed((cascaded.id,))

    assert repository.get_run(cascaded.id).trashed_at is not None
    assert repository.get_run(replacement.id).trashed_at is None


def test_full_purge_removes_the_owned_cycle_and_checkpoints_only(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    other_cycle = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 26),
        make_primary=False,
    )
    repository.trash_runs_detailed(
        (full.id,),
        primary_replacements={full.id: other_cycle.id},
    )
    thread_ids = tuple(
        repository.checkpoint_thread(run_id) for run_id in (full.id, child.id)
    )
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        for index, thread_id in enumerate(thread_ids):
            saver.conn.execute(
                """
                INSERT INTO checkpoints (
                    thread_id, checkpoint_ns, checkpoint_id
                ) VALUES (?, '', ?)
                """,
                (thread_id, f"checkpoint-{index}"),
            )
        saver.conn.commit()

    purged = repository.purge_runs_detailed((full.id,))
    repeated = repository.purge_runs_detailed((full.id,))

    assert purged.changed == 2
    assert purged.impacts[0].affected_run_ids == (full.id, child.id)
    assert repeated.changed == 0
    for run_id in (full.id, child.id):
        with pytest.raises(RunNotFoundError):
            repository.get_run(run_id)
    assert repository.get_run(other_cycle.id).id == other_cycle.id
    with repository.engine.connect() as connection:
        for thread_id in thread_ids:
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).scalar_one() == 0


def test_two_maintenance_connections_purge_one_full_cycle_once(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((full.id,))
    now = datetime(2026, 9, 1, tzinfo=UTC)
    with repository.sessions.begin() as session:
        for run_id in (full.id, child.id):
            session.get(RunRecord, run_id).trashed_at = (
                now - timedelta(days=31)
            ).replace(tzinfo=None)

    def purge() -> int:
        return TrashMaintenance(
            app_settings,
            repository,
            utc_clock=lambda: now,
            batch_size=1,
        ).run_once()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: purge(), range(2)))

    assert sorted(results) == [0, 2]
    for run_id in (full.id, child.id):
        with pytest.raises(RunNotFoundError):
            repository.get_run(run_id)


def test_retention_batch_promotes_cascade_child_to_atomic_full_cycle(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((full.id,))
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
    now = datetime(2026, 9, 1, tzinfo=UTC)
    with repository.sessions.begin() as session:
        session.get(RunRecord, child.id).trashed_at = (
            now - timedelta(days=32)
        ).replace(tzinfo=None)
        session.get(RunRecord, full.id).trashed_at = (
            now - timedelta(days=31)
        ).replace(tzinfo=None)

    purged = repository.purge_expired_trash(
        cutoff=now - timedelta(days=30),
        batch_size=1,
    )

    assert purged == 2
    for run_id in (full.id, child.id):
        with pytest.raises(RunNotFoundError):
            repository.get_run(run_id)


def test_two_connections_linearize_independent_child_and_full_trash(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    barrier = Barrier(2)

    def trash(run_id: str):
        barrier.wait(timeout=5)
        return repository.trash_runs_detailed((run_id,))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(trash, (child.id, full.id)))

    assert sum(result.changed for result in results) == 2
    retained = repository.get_timeline("NVDA", trash_state="all")
    retained_child = next(node for node in retained.nodes if node.id == child.id)

    repository.restore_runs_detailed((full.id,))

    child_after_full_restore = repository.get_run(child.id)
    if retained_child.trash_cascade_full_run_id == full.id:
        assert child_after_full_restore.trashed_at is None
    else:
        assert child_after_full_restore.trashed_at is not None


def test_incremental_restore_revalidates_the_current_full_baseline(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((child.id,))
    with repository.sessions.begin() as session:
        session.get(RunRecord, full.id).research_schema_version = "obsolete"

    with pytest.raises(
        InvalidRunTransitionError,
        match="valid current Full Baseline",
    ):
        repository.restore_runs_detailed((child.id,))

    assert repository.get_run(child.id).trashed_at is not None


def test_two_connections_linearize_restore_against_incremental_retry_slot(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    retained = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((retained.id,))
    request = AnalysisRequest(
        ticker="NVDA",
        analysis_date=date(2026, 7, 25),
        research_kind="incremental",
        full_baseline_run_id=full.id,
    )
    failed, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
        research_schema_version="1",
        information_cutoff_at=datetime(2026, 7, 25, 23, 59, 59, tzinfo=UTC),
        method_snapshot={"schema_version": "1"},
        research_kind="incremental",
        full_baseline_run_id=full.id,
        incremental_input_fingerprint="fingerprint-2026-07-25",
    )
    repository.claim_run(failed.id, "fixture", 30)
    repository.fail(failed.id, RuntimeError("fixture"))
    assert repository.get_run(full.id).is_research_node is True
    assert repository.get_run(failed.id).is_research_node is False
    listed = {run.id: run for run in repository.list_runs().items}
    assert listed[full.id].is_research_node is True
    assert listed[failed.id].is_research_node is False
    barrier = Barrier(2)

    def restore() -> str:
        barrier.wait(timeout=5)
        try:
            repository.restore_runs_detailed((retained.id,))
            return "restored"
        except InvalidRunTransitionError:
            return "slot-won"

    def retry() -> str:
        barrier.wait(timeout=5)
        try:
            return repository.retry(failed.id).id
        except IncrementalRequestConflictError:
            return "restore-won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(restore)
        retry_future = executor.submit(retry)
        outcomes = (restore_future.result(timeout=10), retry_future.result(timeout=10))

    with repository.sessions() as session:
        active_ids = tuple(
            session.scalars(
                select(RunRecord.id).where(
                    RunRecord.full_baseline_run_id == full.id,
                    RunRecord.incremental_cutoff == date(2026, 7, 25),
                    RunRecord.trashed_at.is_(None),
                    RunRecord.status.in_(("queued", "running", "succeeded")),
                )
            )
        )
    assert len(active_ids) == 1
    assert active_ids[0] in {retained.id, failed.id}
    assert outcomes[0] in {"restored", "slot-won"}


def test_incremental_purge_removes_only_its_owned_rows_and_checkpoint(
    repository, app_settings
):
    full = _commit_node(
        repository, app_settings, analysis_date=date(2026, 7, 24)
    )
    child = _commit_node(
        repository,
        app_settings,
        analysis_date=date(2026, 7, 25),
        baseline_id=full.id,
    )
    repository.trash_runs_detailed((child.id,))
    checkpoint_thread = repository.checkpoint_thread(child.id)
    with SqliteSaver.from_conn_string(str(app_settings.database_path)) as saver:
        saver.setup()
        saver.conn.execute(
            """
            INSERT INTO checkpoints (
                thread_id, checkpoint_ns, checkpoint_id
            ) VALUES (?, '', 'incremental-checkpoint')
            """,
            (checkpoint_thread,),
        )
        saver.conn.commit()

    result = repository.purge_runs_detailed((child.id,))

    assert result.changed == 1
    assert repository.get_run(full.id).id == full.id
    with pytest.raises(RunNotFoundError):
        repository.get_run(child.id)
    with repository.sessions() as session:
        for model in (
            ResearchNodeRecord,
            RunAttemptRecord,
            RunEvidenceRecord,
            DecisionRecord,
        ):
            assert session.scalar(
                select(func.count()).select_from(model).where(
                    model.run_id == child.id
                )
            ) == 0
    with repository.engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM checkpoints WHERE thread_id = ?",
            (checkpoint_thread,),
        ).scalar_one() == 0
