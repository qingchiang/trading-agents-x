from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select

from tests.factories import (
    analyst_report,
    research_case,
    research_decision,
)
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystClaimType,
    AnalystReport,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    ClaimImportance,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    DecisionBrief,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceItem,
    IssueDisposition,
    JudgeDraft,
    KeyClaim,
    NumericAuditAppendixStatus,
    NumericAuditComponentType,
    NumericAuditOmission,
    NumericAuditPhase,
    NumericAuditSnapshot,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayStatus,
    NumericRequirementCheck,
    RebuttalReview,
    ReportAuditStatus,
    ReportSection,
    ResearchArtifactDraft,
    ResearchRating,
    RiskReview,
    RunMetrics,
    RunStatus,
    RunTrashState,
)
from tradingagents.application.database import (
    DecisionRecord,
    OutcomeFeedbackRecord,
    OutcomeRecord,
    ReflectionAttemptRecord,
    ReflectionGenerationCycleRecord,
    ReflectionRecord,
    RunAttemptRecord,
    RunRecord,
)
from tradingagents.application.maintenance import TrashMaintenance
from tradingagents.application.repository import (
    ArtifactConflictError,
    EvidenceConflictError,
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


def test_failed_retry_metrics_are_preserved_per_attempt_and_aggregated(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker-1", 30)
    first = RunMetrics(
        llm_calls=2,
        input_tokens=1_000,
        output_tokens=100,
        detailed_usage_calls=1,
        wall_time_seconds=2.5,
    )
    repository.fail(run.id, RuntimeError("first failed"), metrics=first)
    repository.retry(run.id)
    repository.claim_run(run.id, "worker-2", 30)
    second = RunMetrics(
        llm_calls=1,
        input_tokens=400,
        output_tokens=40,
        detailed_usage_calls=1,
        wall_time_seconds=1.25,
    )

    aggregate = repository.fail(
        run.id,
        RuntimeError("second failed"),
        metrics=second,
    )

    assert aggregate.llm_calls == 3
    assert aggregate.input_tokens == 1_400
    assert aggregate.wall_time_seconds == 3.75
    assert repository.get_run(run.id).metrics == aggregate
    attempt_views = repository.list_attempts(run.id)
    assert [attempt.status for attempt in attempt_views] == [
        RunStatus.FAILED,
        RunStatus.FAILED,
    ]
    assert [attempt.metrics.llm_calls for attempt in attempt_views] == [2, 1]
    assert [attempt.error_code for attempt in attempt_views] == [
        "RuntimeError",
        "RuntimeError",
    ]
    with repository.sessions() as session:
        attempts = list(
            session.scalars(
                select(RunAttemptRecord)
                .where(RunAttemptRecord.run_id == run.id)
                .order_by(RunAttemptRecord.attempt)
            )
        )
    assert [
        RunMetrics.model_validate(attempt.metrics_json).llm_calls
        for attempt in attempts
    ] == [2, 1]


def test_interrupted_segments_accumulate_within_the_same_attempt(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker-1", 30)
    repository.release_claim(
        run.id,
        "worker-1",
        metrics=RunMetrics(
            llm_calls=1,
            input_tokens=300,
            wall_time_seconds=1.0,
        ),
    )
    repository.claim_run(run.id, "worker-2", 30)
    aggregate = repository.fail(
        run.id,
        RuntimeError("failed after resume"),
        metrics=RunMetrics(
            llm_calls=2,
            input_tokens=700,
            wall_time_seconds=2.0,
        ),
    )

    assert aggregate.llm_calls == 3
    assert aggregate.input_tokens == 1_000
    assert aggregate.wall_time_seconds == 3.0
    with repository.sessions() as session:
        attempt = session.scalar(
            select(RunAttemptRecord).where(RunAttemptRecord.run_id == run.id)
        )
    assert RunMetrics.model_validate(attempt.metrics_json) == aggregate
    attempt_view = repository.list_attempts(run.id)[0]
    assert attempt_view.resume_count == 1
    assert attempt_view.metrics == aggregate


def test_release_claim_requeues_same_attempt_and_checkpoint(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    queued, _ = _create(repository, app_settings)
    checkpoint = repository.checkpoint_thread(queued.id)
    repository.claim_run(queued.id, "worker-a", 30)

    released = repository.release_claim(queued.id, "worker-a")

    assert released.status is RunStatus.QUEUED
    assert released.attempt == 1
    assert repository.checkpoint_thread(queued.id) == checkpoint
    reclaimed = repository.claim_next("worker-b", 30)
    assert reclaimed is not None
    assert reclaimed.id == queued.id
    assert reclaimed.attempt == 1


def test_release_claim_requires_current_lease_owner(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    queued, _ = _create(repository, app_settings)
    repository.claim_run(queued.id, "worker-a", 30)

    with pytest.raises(InvalidRunTransitionError):
        repository.release_claim(queued.id, "worker-b")


def test_events_are_monotonic_replayable_and_redacted(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    first = repository.append_event(
        run.id,
        "run.queued",
        payload={
            "api_key": "private",
            "message": "Authorization: Bearer private-token",
        },
    )
    second = repository.append_event(run.id, "run.started", node="market")

    replay = repository.list_events(run.id, after_sequence=1)

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.sequence for event in replay] == [2]
    stored = repository.list_events(run.id)[0].payload
    assert stored["api_key"] == "[REDACTED]"
    assert stored["message"] == "Authorization: [REDACTED]"


def test_trash_restore_filters_are_atomic_and_idempotent(
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
        repository.trash_runs((terminal.id, queued.id))

    assert repository.get_run(terminal.id).trashed_at is None
    trashed, changed = repository.trash_runs((terminal.id,))
    repeated, changed_again = repository.trash_runs((terminal.id,))

    assert changed == 1
    assert changed_again == 0
    assert trashed[0].trashed_at is not None
    assert repeated[0].trashed_at == trashed[0].trashed_at
    active_page = repository.list_runs()
    assert [item.id for item in active_page.items] == [queued.id]
    assert active_page.items[0].research_rating is None
    trashed_page = repository.list_runs(
        trash_state=RunTrashState.TRASHED,
        q="nv",
    )
    assert trashed_page.total == 1
    assert trashed_page.items[0].id == terminal.id
    all_page = repository.list_runs(
        trash_state=RunTrashState.ALL,
        limit=1,
        offset=1,
    )
    assert all_page.total == 2
    assert len(all_page.items) == 1

    restored, restored_changed = repository.restore_runs((terminal.id,))
    _, restored_again = repository.restore_runs((terminal.id,))

    assert restored_changed == 1
    assert restored_again == 0
    assert restored[0].trashed_at is None
    assert repository.list_runs().total == 2


def test_recent_instruments_are_deduplicated_and_exclude_trashed_runs(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    older, _ = _create(repository, app_settings, "NVDA")
    trashed, _ = _create(repository, app_settings, "AAPL")
    latest, _ = _create(repository, app_settings, "NVDA")
    repository.set_instrument_name(older.id, "NVIDIA Corporation")
    repository.set_instrument_name(trashed.id, "Apple")
    repository.set_instrument_name(latest.id, "NVIDIA")
    repository.set_instrument_local_name(latest.id, "英伟达")
    with repository.sessions.begin() as session:
        session.get(RunRecord, older.id).created_at = datetime(2026, 7, 1)
        session.get(RunRecord, trashed.id).created_at = datetime(2026, 7, 2)
        session.get(RunRecord, latest.id).created_at = datetime(2026, 7, 3)
    repository.request_cancel(trashed.id)
    repository.trash_runs((trashed.id,))

    recent = repository.recent_instruments()

    assert [(item.ticker, item.instrument_name) for item in recent] == [
        ("NVDA", "NVIDIA")
    ]
    assert recent[0].instrument_local_name == "英伟达"
    assert recent[0].last_used_at == datetime(
        2026,
        7,
        3,
        tzinfo=timezone.utc,
    )
    assert repository.list_runs(q="nvidia").total == 2
    assert repository.list_runs(q="英伟达").total == 1


def test_artifacts_are_typed_retained_and_idempotent_across_retries(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker-1", 30)
    report = analyst_report(
        executive_summary="Fixture summary.",
        confidence=0.8,
        narrative="Fixture narrative must not enter event payloads.",
    )
    draft = ResearchArtifactDraft(
        node="analyst.market",
        stage="analyst",
        role="market",
        generation_method=ArtifactGenerationMethod.TOOL_CALL,
        generation_observations=(
            ArtifactGenerationObservation(
                node="analyst.market.serialize",
                task_kind="schema_serialization",
                client_role="quick_serializer",
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
            ),
        ),
        content=report,
    )

    first, first_event = repository.append_artifact(run.id, draft)
    duplicate, duplicate_event = repository.append_artifact(run.id, draft)
    repository.fail(run.id, RuntimeError("retry fixture"))
    repository.retry(run.id)
    repository.claim_run(run.id, "worker-2", 30)
    retried, retried_event = repository.append_artifact(run.id, draft)
    with pytest.raises(ArtifactConflictError):
        repository.append_artifact(
            run.id,
            draft.model_copy(
                update={
                    "content": report.model_copy(
                        update={
                            "markdown": "# Overview\n\nRecomputed summary."
                        }
                    )
                }
            ),
        )

    assert first == duplicate == retried
    assert first.attempt == 1
    assert first.generation_observations == draft.generation_observations
    assert duplicate_event is None
    assert retried_event is None
    assert first_event is not None
    assert [artifact.id for artifact in repository.list_artifacts(run.id)] == [
        first.id,
    ]
    events = repository.list_events(run.id)
    assert [event.event_type for event in events] == [
        "artifact.created",
    ]
    assert events[0].payload == {
        "artifact_id": first.id,
        "attempt": 1,
        "stage": "analyst",
        "role": "market",
        "round": 0,
            "schema_version": "2",
        "prompt_version": "research-v1",
        "generation_method": "tool_call",
        "generation_observations": [
            {
                "node": "analyst.market.serialize",
                "task_kind": "schema_serialization",
                "client_role": "quick_serializer",
                "generation_method": "tool_call",
            }
        ],
        "content_type": "analyst_report",
    }
    assert "Fixture narrative" not in str(events[0].payload)


def test_artifact_prompt_version_is_audited_and_part_of_identity(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    report = analyst_report(
        confidence=0.8,
        narrative="Fixture narrative.",
    )
    base = ResearchArtifactDraft(
        node="analyst.market",
        stage="analyst",
        role="market",
        prompt_version="market-v1",
        generation_method=ArtifactGenerationMethod.TOOL_CALL,
        content=report,
    )

    first, _ = repository.append_artifact(run.id, base)
    second, _ = repository.append_artifact(
        run.id,
        base.model_copy(update={"prompt_version": "market-v2"}),
    )

    assert first.id != second.id
    assert [item.prompt_version for item in repository.list_artifacts(run.id)] == [
        "market-v1",
        "market-v2",
    ]


@pytest.mark.parametrize(
    ("content", "content_type", "stage", "role"),
    (
        (
            DecisionBrief(
                markdown="# Decision synthesis\n\nDraft reasoning.",
                evidence_refs=(),
            ),
            "decision_brief",
            "decision_brief",
            "final_committee",
        ),
        (research_case(role="bull"), "research_case", "case", "bull"),
        (
            DebateAgenda(
                summary="Fixture agenda.",
                issues=(
                    DebateIssue(
                        id="debate.issue_1",
                        question="Which mechanism dominates?",
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            ),
            "debate_agenda",
            "agenda",
            "moderator",
        ),
        (
            RebuttalReview(
                role="bull",
                round=1,
                markdown="The mechanism remains plausible.",
                addressed_issue_ids=("debate.issue_1",),
                open_issue_ids=("debate.issue_1",),
            ),
            "rebuttal_review",
            "rebuttal",
            "bull",
        ),
        (
            JudgeDraft(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.6,
                markdown="Both cases retain support.",
                issue_dispositions=(
                    IssueDisposition(
                        issue_id="debate.issue_1",
                        status="unresolved",
                    ),
                ),
            ),
            "judge_draft",
            "judge",
            "research_judge",
        ),
        (
            RiskReview(
                role="integrated",
                markdown="Confidence needs calibration.",
                challenged_issue_ids=("debate.issue_1",),
                unresolved_issue_ids=("debate.issue_1",),
            ),
            "risk_review",
            "risk",
            "integrated",
        ),
        (
            research_decision(),
            "research_decision",
            "decision",
            "final_committee",
        ),
    ),
)
def test_deliberation_artifact_types_round_trip(
    repository: RunRepository,
    app_settings: AppSettings,
    content,
    content_type: str,
    stage: str,
    role: str,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)

    stored, _ = repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node=f"{stage}.{role}",
            stage=stage,
            role=role,
            round=1 if stage == "rebuttal" else 0,
            prompt_version=f"{stage}-v2",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=content,
        ),
    )
    restored = repository.list_artifacts(run.id)

    assert stored.content_type == content_type
    assert restored == [stored]


def test_recovery_events_surface_as_audit_notices_not_top_level_warnings(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="committee.final",
            stage="decision",
            role="final_committee",
            generation_method=ArtifactGenerationMethod.RAW_JSON_RECOVERED,
            content=research_decision(
                confidence=0.5,
                thesis="Fixture recovered thesis.",
                evidence_refs=(),
                risks=("Fixture risk.",),
                invalidation_conditions=("Fixture invalidation.",),
                time_horizon="6-12 months",
            ),
        ),
    )
    repository.append_event(
        run.id,
        "node.output_retry",
        node="committee.final.serialize.core",
        payload={
            "method": "raw_json_recovered",
            "reason_code": "schema_validation",
            "validation_issues": ["schema.thesis"],
        },
    )
    repository.append_event(
        run.id,
        "node.output_recovered",
        node="committee.final.serialize.core",
        payload={
            "method": "raw_json_recovered",
            "reason_code": "schema_validation",
        },
    )

    result = repository.get_result(run.id)

    assert result.warnings == ()
    assert len(result.recoveries) == 1
    assert result.recoveries[0].node == "committee.final.serialize.core"
    assert result.recoveries[0].validation_issue_codes == ("schema.thesis",)


def test_partial_numeric_audit_surfaces_after_result_reload(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="committee.final",
            stage="decision",
            role="final_committee",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=research_decision().model_copy(
                update={"numeric_audit_status": NumericAuditStatus.PARTIAL}
            ),
        ),
    )

    result = repository.get_result(run.id)

    assert [warning.code for warning in result.warnings] == [
        "decision.numeric_audit_partial"
    ]


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
        markdown="# Overview\n\nMarket report.",
        report_sections=(
            ReportSection(
                id="market.section.overview",
                title="Overview",
                anchor="overview",
                source_refs=(evidence_item.ref,),
            ),
        ),
        key_claims=(
            KeyClaim(
                id="market.claim_1",
                section_id="market.section.overview",
                kind=AnalystClaimType.OBSERVATION,
                importance=ClaimImportance.PRIMARY,
                statement="Price closed at 100.",
                implication="Momentum remains constructive.",
                confidence=0.7,
                evidence_refs=(evidence_item.ref,),
            ),
        ),
        confidence=0.7,
        source_refs=(evidence_item.ref,),
        audit_status=ReportAuditStatus.COMPLETE,
        warnings=("**Historical price** was `partial`.",),
    )
    decision = research_decision(
        rating=ResearchRating.OVERWEIGHT,
        confidence=0.7,
        thesis="Constructive evidence outweighs valuation risk.",
        evidence_refs=(evidence_item.ref,),
        catalysts=("Earnings execution",),
        risks=("Multiple compression",),
        invalidation_conditions=("Growth misses expectations",),
        time_horizon="6-12 months",
    ).model_copy(update={"numeric_audit_status": NumericAuditStatus.PARTIAL})
    numeric_audit = DecisionNumericAuditAppendix(
        status=NumericAuditAppendixStatus.PARTIAL,
        requirement_checks=(
            NumericRequirementCheck(
                requirement_id="req_forward_pe",
                calculation_id="calc_forward_pe",
                component_path="thesis",
                label="Forward PE",
                stated_value=45.8,
                fraction_digits=1,
                unit="x",
                formula="close_price / eps",
                inputs={"close_price": 4000.0, "eps": 87.35},
                input_evidence_refs=(evidence_item.ref,),
                canonical_result=45.79278763594734,
                comparison_result=45.79278763594734,
                comparison_difference=45.79278763594734 - 45.8,
                rounded_stated_value=45.8,
                rounded_canonical_result=45.8,
                calculation_status=NumericCalculationStatus.VERIFIED,
                display_status=NumericDisplayStatus.MATCHED,
            ),
        ),
        snapshots=(
            NumericAuditSnapshot(
                phase=NumericAuditPhase.REPAIR,
                method=ArtifactGenerationMethod.TOOL_CALL_RECOVERED,
                reason_code="semantic_validation",
                validation_issues=(
                    "semantic.numeric.calculation.calc_1.formula.invalid_syntax",
                ),
                schema_valid=True,
                candidate={
                    "requested": True,
                    "appendix_only_marker": "must-not-enter-memory",
                    "calculation_records": [],
                },
                candidate_digest="a" * 64,
            ),
        ),
        omitted_components=(
            NumericAuditOmission(
                component_path="numeric.calculation.calc_1",
                component_type=NumericAuditComponentType.CALCULATION,
                reference_label="calc_1",
                issue_codes=(
                    "numeric.calculation.calc_1.formula.invalid_syntax",
                ),
            ),
        ),
    )
    result = AnalysisResult(
        run_id=run.id,
        status=RunStatus.SUCCEEDED,
        instrument="NVDA",
        reports={"market": report},
        decision=decision,
        numeric_audit=numeric_audit,
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
    sealed, event = repository.seal_evidence(run.id, evidence)
    duplicate, duplicate_event = repository.seal_evidence(run.id, evidence)
    assert sealed == duplicate
    assert event is not None
    assert duplicate_event is None
    assert repository.evidence_status(run.id) == sealed
    assert repository.get_evidence(run.id) == evidence
    conflicting_item = EvidenceItem.create(
        source="fixture",
        evidence_type="price",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        value=101.0,
        unit="USD",
    )
    with pytest.raises(EvidenceConflictError):
        repository.seal_evidence(
            run.id,
            EvidenceBundle(
                instrument="NVDA",
                analysis_date=date(2026, 7, 24),
                items=(conflicting_item,),
            ),
        )

    repository.complete(run.id, result, evidence=evidence, benchmark="SPY")
    restored = repository.get_result(run.id)
    due_at = datetime(2100, 1, 1, tzinfo=timezone.utc)
    pending = repository.pending_outcomes(due_at=due_at)
    repository.trash_runs((run.id,))
    assert repository.pending_outcomes(due_at=due_at) == []
    assert repository.review_entries() == []
    repository.restore_runs((run.id,))
    assert repository.pending_outcomes(due_at=due_at)[0]["outcome_id"] == (
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
    repository.resolve_outcome(
        pending[0]["outcome_id"],
        observation_start=date(2026, 7, 25),
        observation_end=date(2026, 8, 1),
        raw_return=0.08,
        alpha_return=0.03,
        reflection="A replay must not replace the generated Reflection.",
    )

    assert restored.status is RunStatus.SUCCEEDED
    assert restored.decision == decision
    assert restored.numeric_audit == numeric_audit
    assert restored.evidence == evidence
    assert isinstance(restored.reports["market"], AnalystReport)
    assert restored.warnings[0].message == "Historical price was partial."
    context = repository.memory_context("NVDA", "stock")
    assert len(context.items) == 1
    assert context.items[0].ticker == "NVDA"
    assert "The thesis worked" in context.items[0].reflection
    assert "appendix_only_marker" not in context.prompt_text()
    repository.trash_runs((run.id,))
    assert repository.memory_context("NVDA", "stock").items == ()
    assert repository.review_entries() == []
    repository.restore_runs((run.id,))
    assert repository.memory_context("NVDA", "stock").items[0].run_id == run.id

    with repository.sessions() as session:
        outcome = session.scalar(select(OutcomeRecord))
        reflection = session.scalar(select(ReflectionRecord))
        feedback = session.scalar(select(OutcomeFeedbackRecord))
        assert outcome is not None
        assert outcome.method_version == "short_term_relative_return.v1"
        assert outcome.method_category == "short_term_relative_return"
        assert outcome.market_timezone == "America/New_York"
        assert "do not prove or disprove" in outcome.horizon_limit
        assert outcome.data_available_at == outcome.resolved_at
        assert reflection is not None
        assert reflection.status == "generated"
        assert reflection.text == "The thesis worked because earnings accelerated."
        assert feedback is not None
        assert feedback.status in {"eligible", "ineligible"}
        assert (
            feedback.qualification_policy_version
            == "outcome_feedback_qualification.v1"
        )
        assert feedback.available_at == max(
            outcome.data_available_at,
            reflection.generated_at,
            feedback.qualified_at,
        )
        assert session.scalar(select(func.count()).select_from(ReflectionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(OutcomeFeedbackRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ReflectionGenerationCycleRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ReflectionAttemptRecord)) == 1

    feedback_view = repository.review_entries()[0]["outcome_feedback"]
    assert feedback_view["qualification_policy_version"] == (
        "outcome_feedback_qualification.v1"
    )
    assert "reflection" not in repository.review_entries()[0]
    detail = repository.review_audit_detail(pending[0]["outcome_id"])
    assert detail is not None
    assert detail["reflection"] == "The thesis worked because earnings accelerated."
    assert detail["aggregate_usage"] == {
        "usage_status": "not_reported",
        "attempt_count": 1,
        "llm_calls": 1,
        "input_tokens": None,
        "output_tokens": None,
        "cache_hit_input_tokens": None,
        "cache_miss_input_tokens": None,
        "reasoning_output_tokens": None,
        "wall_time_seconds": None,
        "provider_reported_cost_usd": None,
    }
    assert detail["attempts"][0]["attempt_kind"] == "unstructured"
    assert detail["attempts"][0]["attempt_schema_version"] == (
        "outcome_reflection_attempt.v1"
    )
    assert detail["attempts"][0]["candidate_schema_version"] == (
        "outcome_reflection_legacy_unstructured.v1"
    )

    with repository.sessions.begin() as session:
        reflection = session.scalar(select(ReflectionRecord))
        cycle = session.scalar(select(ReflectionGenerationCycleRecord))
        assert reflection is not None
        assert cycle is not None
        session.add(
            ReflectionAttemptRecord(
                reflection_id=reflection.id,
                generation_cycle_id=cycle.id,
                sequence=2,
                trigger="repair",
                origin="automatic",
                attempt_kind="repair",
                started_at=datetime(2026, 8, 2),
                finished_at=datetime(2026, 8, 2, 0, 0, 1),
                outcome="generated",
                candidate_schema_version="outcome_reflection.v1",
                usage_status="reported",
                llm_calls=1,
                input_tokens=10,
                output_tokens=4,
                wall_time_seconds=1.0,
            )
        )

    mixed_usage = repository.review_audit_detail(pending[0]["outcome_id"])
    assert mixed_usage is not None
    assert mixed_usage["aggregate_usage"] == {
        "usage_status": "not_reported",
        "attempt_count": 2,
        "llm_calls": 2,
        "input_tokens": None,
        "output_tokens": None,
        "cache_hit_input_tokens": None,
        "cache_miss_input_tokens": None,
        "reasoning_output_tokens": None,
        "wall_time_seconds": None,
        "provider_reported_cost_usd": None,
    }

    with repository.sessions() as session:
        record = session.scalar(
            select(DecisionRecord).where(DecisionRecord.run_id == run.id)
        )
        assert record is not None
        historical_audit = dict(record.numeric_audit_json or {})
        historical_checks = []
        for item in historical_audit.get("requirement_checks", []):
            historical = dict(item)
            historical.pop("display_scale", None)
            historical.pop("comparison_result", None)
            historical.pop("comparison_difference", None)
            historical.pop("date_evidence_refs", None)
            historical_checks.append(historical)
        record.numeric_audit_json = {
            **historical_audit,
            "requirement_checks": historical_checks,
        }
        session.commit()

    historical_result = repository.get_result(run.id)
    assert historical_result.numeric_audit is not None
    historical_check = historical_result.numeric_audit.requirement_checks[0]
    assert historical_check.comparison_result is None
    assert historical_check.comparison_difference is None


def test_failed_run_retains_sealed_evidence_and_analyst_reports(
    repository: RunRepository,
    app_settings: AppSettings,
) -> None:
    run, _ = _create(repository, app_settings)
    repository.claim_run(run.id, "worker", 30)
    item = EvidenceItem.create(
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
        items=(item,),
    )
    report = analyst_report(evidence_ref=item.ref)
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

    repository.fail(run.id, RuntimeError("final decision validation failed"))

    restored = repository.get_result(run.id)
    assert restored.status is RunStatus.FAILED
    assert restored.evidence == evidence
    assert restored.reports == {"market": report}
    assert restored.decision is None


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
    repository.trash_runs((source.id,))
    with repository.sessions.begin() as session:
        session.get(RunRecord, source.id).trashed_at = datetime(2026, 7, 1)
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
        return TrashMaintenance(
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
    reports = {
        name: analyst_report(
            analyst=name,
            narrative=f"# Overview\n\n{name.title()} report.",
        )
        for name in ("social", "news", "market", "fundamentals")
    }
    for name, report in reports.items():
        repository.append_artifact(
            run.id,
            ResearchArtifactDraft(
                node=f"analyst.{name}",
                stage="analyst",
                role=name,
                generation_method=ArtifactGenerationMethod.TOOL_CALL,
                content=report,
            ),
        )
    result = AnalysisResult(
        run_id=run.id,
        status=RunStatus.SUCCEEDED,
        instrument="NVDA",
        reports=reports,
        decision=None,
    )

    assert list(result.reports) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]

    repository.seal_evidence(run.id, evidence)
    repository.complete(run.id, result, evidence=evidence, benchmark="SPY")
    restored = repository.get_result(run.id)

    assert list(restored.reports) == [
        "fundamentals",
        "market",
        "news",
        "social",
    ]
