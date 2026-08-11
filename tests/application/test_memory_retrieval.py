from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from tests.factories import research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    EvidenceBundle,
    ResearchRating,
    RunProfile,
    RunStatus,
)
from tradingagents.application.database import (
    DecisionRecord,
    OutcomeFeedbackRecord,
    OutcomeRecord,
    ReflectionAttemptRecord,
    ReflectionGenerationCycleRecord,
    ReflectionRecord,
)
from tradingagents.application.outcome_feedback import OutcomeFeedbackRetirementReason
from tradingagents.application.repository import (
    OutcomeFeedbackRetirementConflictError,
    OutcomeFeedbackRetirementNotFoundError,
    OutcomeReflectionRegenerationConflictError,
    RunRepository,
)


def _seed_memory(
    repository: RunRepository,
    *,
    ticker: str,
    analysis_date: date,
    reflection: str,
    thesis: str,
    resolved: bool = True,
    rating: ResearchRating = ResearchRating.HOLD,
    catalysts: tuple[str, ...] = (),
    risks: tuple[str, ...] = (),
    invalidation_conditions: tuple[str, ...] = (),
    time_horizon: str = "Fixture horizon",
) -> str:
    request = AnalysisRequest(
        ticker=ticker,
        analysis_date=analysis_date,
        analysts=("market",),
    )
    decision = research_decision(
        rating=rating,
        confidence=0.5,
        thesis=thesis,
        evidence_refs=(),
        catalysts=catalysts,
        risks=risks or ("Legacy fixture risks were not recorded.",),
        invalidation_conditions=(
            invalidation_conditions
            or ("Legacy fixture invalidation was not recorded.",)
        ),
        time_horizon=time_horizon,
    )
    decision = decision.model_copy(
        update={
            "scenarios": tuple(
                scenario.model_copy(
                    update={"outcome": f"{ticker} {scenario.outcome}"}
                )
                for scenario in decision.scenarios
            )
        }
    )
    run, _ = repository.create_run(request, {"fixture": True})
    repository.claim_run(run.id, "fixture-worker", 30)
    evidence = EvidenceBundle(
        instrument=request.ticker,
        analysis_date=request.analysis_date,
        items=(),
    )
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument=request.ticker,
            reports={},
            decision=decision,
            evidence=evidence,
        ),
        evidence=evidence,
        benchmark="SPY",
    )
    if resolved:
        with repository.sessions() as session:
            outcome_id = session.scalar(
                select(OutcomeRecord.id)
                .join(DecisionRecord)
                .where(DecisionRecord.run_id == run.id)
            )
        assert outcome_id is not None
        repository.resolve_outcome(
            outcome_id,
            observation_start=date(2026, 7, 1),
            observation_end=date(2026, 7, 8),
            raw_return=0.01,
            alpha_return=0.005,
            reflection=reflection,
        )
    return run.id


def _feedback_for_run(repository: RunRepository, run_id: str) -> tuple[int, int]:
    with repository.sessions() as session:
        outcome = session.scalar(
            select(OutcomeRecord)
            .join(DecisionRecord)
            .where(DecisionRecord.run_id == run_id)
        )
        assert outcome is not None
        reflection = session.scalar(
            select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome.id)
        )
        assert reflection is not None
        feedback = session.scalar(
            select(OutcomeFeedbackRecord).where(
                OutcomeFeedbackRecord.reflection_id == reflection.id
            )
        )
        assert feedback is not None
        return outcome.id, feedback.id


def test_retire_eligible_feedback_is_auditable_and_idempotent(
    repository: RunRepository,
) -> None:
    run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        reflection="Method lesson: Compare the method limits before reuse.",
        thesis="Fixture thesis for Feedback retirement.",
    )
    outcome_id, feedback_id = _feedback_for_run(repository, run_id)
    with repository.sessions.begin() as session:
        feedback = session.get(OutcomeFeedbackRecord, feedback_id)
        assert feedback is not None
        feedback.status = "eligible"
        feedback.reasons_json = []

    retired = repository.retire_outcome_feedback(
        feedback_id,
        reason=OutcomeFeedbackRetirementReason.MISLEADING,
        note="It generalizes a one-off result.",
    )
    repeated = repository.retire_outcome_feedback(
        feedback_id,
        reason=OutcomeFeedbackRetirementReason.NOT_USEFUL,
        note="A retry must retain the original audit record.",
    )

    assert repeated == retired
    assert retired["status"] == "retired"
    assert retired["retirement_reason"] == "misleading"
    assert retired["retirement_note"] == "It generalizes a one-off result."
    review = repository.review_entries(outcome_id=outcome_id)[0]
    assert review["review_status"] == "feedback_retired"
    feedback_view = review["outcome_feedback"]
    assert feedback_view is not None
    assert feedback_view["status"] == "retired"
    assert feedback_view["retirement_reason"] == "misleading"
    assert feedback_view["retirement_note"] == "It generalizes a one-off result."
    assert feedback_view["reasons"] == []


def test_retire_feedback_rejects_missing_ineligible_and_inconsistent_lifecycles(
    repository: RunRepository,
) -> None:
    with pytest.raises(OutcomeFeedbackRetirementNotFoundError):
        repository.retire_outcome_feedback(
            999,
            reason=OutcomeFeedbackRetirementReason.NOT_USEFUL,
            note=None,
        )

    run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        reflection="Method lesson: Use a different market window.",
        thesis="Fixture thesis for forbidden Feedback retirement.",
    )
    _outcome_id, feedback_id = _feedback_for_run(repository, run_id)
    with repository.sessions.begin() as session:
        feedback = session.get(OutcomeFeedbackRecord, feedback_id)
        assert feedback is not None
        feedback.status = "ineligible"
    with pytest.raises(OutcomeFeedbackRetirementConflictError):
        repository.retire_outcome_feedback(
            feedback_id,
            reason=OutcomeFeedbackRetirementReason.TOO_SPECIFIC,
            note=None,
        )

    with repository.sessions.begin() as session:
        feedback = session.get(OutcomeFeedbackRecord, feedback_id)
        reflection = session.get(ReflectionRecord, feedback.reflection_id) if feedback else None
        assert feedback is not None and reflection is not None
        feedback.status = "eligible"
        reflection.status = "invalid"
    with pytest.raises(OutcomeFeedbackRetirementConflictError):
        repository.retire_outcome_feedback(
            feedback_id,
            reason=OutcomeFeedbackRetirementReason.OTHER,
            note=None,
        )


def test_reflection_regeneration_is_idempotent_and_auto_retries_are_bounded(
    repository: RunRepository,
) -> None:
    run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 24),
        reflection="Method lesson: legacy fixture.",
        thesis="Fixture thesis for manual regeneration.",
    )
    with repository.sessions.begin() as session:
        outcome = session.scalar(
            select(OutcomeRecord)
            .join(DecisionRecord)
            .where(DecisionRecord.run_id == run_id)
        )
        assert outcome is not None
        reflection = session.scalar(
            select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome.id)
        )
        feedback = session.scalar(
            select(OutcomeFeedbackRecord).where(
                OutcomeFeedbackRecord.reflection_id == reflection.id
            )
        )
        assert reflection is not None and feedback is not None
        session.delete(feedback)
        reflection.status = "invalid"
        reflection.next_retry_at = None
        outcome_id = outcome.id

    queued_at = datetime(2026, 8, 5, 0, 0)
    first = repository.enqueue_outcome_reflection_regeneration(
        outcome_id, idempotency_key="manual-retry-1", queued_at=queued_at
    )
    same = repository.enqueue_outcome_reflection_regeneration(
        outcome_id, idempotency_key="manual-retry-1", queued_at=queued_at
    )
    assert first["cycle"]["id"] == same["cycle"]["id"]
    assert first["cycle"]["status"] == "queued"
    assert first["review_status"] == "awaiting_reflection"
    assert first["reflection_status"] == "pending"
    with pytest.raises(OutcomeReflectionRegenerationConflictError) as conflict:
        repository.enqueue_outcome_reflection_regeneration(
            outcome_id, idempotency_key="manual-retry-2", queued_at=queued_at
        )
    assert conflict.value.active_cycle_id == first["cycle"]["id"]

    attempt_ids = repository.start_outcome_reflection_attempt(
        outcome_id, started_at=queued_at
    )
    assert attempt_ids is not None
    with repository.sessions() as session:
        manual_attempt = session.get(
            ReflectionAttemptRecord, attempt_ids["attempt_id"]
        )
        assert manual_attempt is not None
        assert manual_attempt.origin == "manual"
        assert manual_attempt.trigger == "user_regeneration"
    repository.mark_reflection_failure(
        outcome_id,
        attempted_at=queued_at,
        error_code="TransportError",
        attempt_ids=attempt_ids,
    )
    with repository.sessions() as session:
        cycle = session.get(ReflectionGenerationCycleRecord, first["cycle"]["id"])
        assert cycle is not None and cycle.status == "failed"
        reflection = session.scalar(
            select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
        )
        assert reflection is not None
        assert reflection.next_retry_at is None

    with repository.sessions.begin() as session:
        reflection = session.scalar(
            select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
        )
        assert reflection is not None
        reflection.status = "pending"
        reflection.current_generation_cycle_id = None
    delays = []
    started_at = queued_at
    for _ in range(4):
        automatic_attempt = repository.start_outcome_reflection_attempt(
            outcome_id, started_at=started_at
        )
        assert automatic_attempt is not None
        repository.mark_reflection_failure(
            outcome_id,
            attempted_at=started_at,
            error_code="TransportError",
            attempt_ids=automatic_attempt,
        )
        with repository.sessions() as session:
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            assert reflection is not None
            if reflection.next_retry_at is not None:
                delays.append(reflection.next_retry_at - started_at)
                started_at = reflection.next_retry_at
    assert delays == [
        timedelta(hours=1),
        timedelta(hours=6),
        timedelta(hours=24),
    ]
    assert repository.review_entries(outcome_id=outcome_id)[0]["review_status"] == "reflection_failed"

    with repository.sessions.begin() as session:
        reflection = session.scalar(
            select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
        )
        assert reflection is not None
        reflection.status = "pending"
        reflection.current_generation_cycle_id = None
    barrier = Barrier(2)

    def claim(worker: str):
        barrier.wait(timeout=5)
        return repository.start_outcome_reflection_attempt(
            outcome_id,
            started_at=started_at,
            trigger=worker,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-a", "worker-b")))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_memory_context_uses_deterministic_same_and_cross_ticker_limits(
    repository: RunRepository,
    monkeypatch,
) -> None:
    fixed_now = datetime(2026, 7, 24, 12, 0, 0)
    monkeypatch.setattr(
        "tradingagents.application.repository._utc_naive",
        lambda: fixed_now,
    )
    same_run_ids = []
    for index in range(1, 7):
        same_run_ids.append(
            _seed_memory(
                repository,
                ticker="NVDA",
                analysis_date=date(2026, 6, index),
                reflection=f"same-reflection-{index}",
                thesis=f"same-decision-{index}",
            )
        )
    cross_run_ids = []
    for index in range(1, 5):
        cross_run_ids.append(
            _seed_memory(
                repository,
                ticker="AAPL",
                analysis_date=date(2026, 5, index),
                reflection=f"cross-reflection-{index}",
                thesis=f"cross-decision-{index}",
            )
        )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 5, 1),
        reflection="japan-reflection",
        thesis="japan-decision",
    )
    _seed_memory(
        repository,
        ticker="600519.SS",
        analysis_date=date(2026, 5, 1),
        reflection="china-reflection",
        thesis="china-decision",
    )
    _seed_memory(
        repository,
        ticker="MSFT",
        analysis_date=date(2026, 5, 1),
        reflection="pending-reflection",
        thesis="pending-decision",
        resolved=False,
    )

    context = repository.memory_context("NVDA", "stock")

    same = [item for item in context.items if item.scope == "same_ticker"]
    cross = [item for item in context.items if item.scope == "same_market"]
    assert [item.reflection for item in same] == [
        f"same-reflection-{index}" for index in range(6, 1, -1)
    ]
    assert [item.decision.thesis for item in same if item.decision] == [
        f"same-decision-{index}" for index in range(6, 1, -1)
    ]
    assert [item.reflection for item in cross] == [
        f"cross-reflection-{index}" for index in range(4, 1, -1)
    ]
    assert all(item.decision is None and item.outcome is None for item in cross)
    assert context.refs == tuple(
        f"memory:{run_id}"
        for run_id in (
            *reversed(same_run_ids[1:]),
            *reversed(cross_run_ids[1:]),
        )
    )
    prompt = context.prompt_text()
    assert "same-reflection-1" not in prompt
    assert "cross-decision-4" not in prompt
    assert "japan-reflection" not in prompt
    assert "china-reflection" not in prompt
    assert "pending-reflection" not in prompt


def test_china_cross_ticker_memory_shares_market_without_crossing_regions(
    repository: RunRepository,
) -> None:
    _seed_memory(
        repository,
        ticker="600519.SS",
        analysis_date=date(2026, 7, 1),
        reflection="Shanghai lesson",
        thesis="Shanghai decision",
    )
    _seed_memory(
        repository,
        ticker="000001.SZ",
        analysis_date=date(2026, 7, 2),
        reflection="Shenzhen lesson",
        thesis="Shenzhen decision",
    )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 7, 3),
        reflection="Tokyo lesson",
        thesis="Tokyo decision",
    )

    context = repository.memory_context("600000.SS", "stock")

    assert {item.reflection for item in context.items} == {
        "Shanghai lesson",
        "Shenzhen lesson",
    }
    assert all(item.scope == "same_market" for item in context.items)
    assert all(item.decision is None and item.outcome is None for item in context.items)


@pytest.mark.parametrize(
    ("ticker", "expected"),
    (
        ("NVDA", "America/New_York"),
        ("SPY", "America/New_York"),
        ("7203.T", "Asia/Tokyo"),
        ("600519.SS", "Asia/Shanghai"),
        ("000001.SZ", "Asia/Shanghai"),
    ),
)
def test_memory_market_bucket(
    ticker,
    expected,
) -> None:
    assert RunRepository.market_bucket(ticker) == expected


@pytest.mark.parametrize(
    ("same_limit", "cross_limit", "same_present", "cross_present"),
    (
        (0, 0, False, False),
        (1, 0, True, False),
        (0, 1, False, True),
        (1, 1, True, True),
    ),
)
def test_memory_limits_can_disable_each_context_class(
    repository: RunRepository,
    same_limit,
    cross_limit,
    same_present,
    cross_present,
) -> None:
    _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="same lesson",
        thesis="same decision",
    )
    _seed_memory(
        repository,
        ticker="AAPL",
        analysis_date=date(2026, 7, 2),
        reflection="cross lesson",
        thesis="cross decision",
    )

    context = repository.memory_context(
        "NVDA",
        "stock",
        same_limit=same_limit,
        cross_limit=cross_limit,
    )

    reflections = {item.reflection for item in context.items}
    assert ("same lesson" in reflections) is same_present
    assert ("cross lesson" in reflections) is cross_present


def test_memory_prompt_is_bounded_per_item_and_in_total(
    repository: RunRepository,
) -> None:
    for index in range(8):
        _seed_memory(
            repository,
            ticker="NVDA" if index < 5 else "AAPL",
            analysis_date=date(2026, 7, index + 1),
            reflection=f"reflection-{index}-" + ("x" * 5_000),
            thesis=f"thesis-{index}-" + ("y" * 5_000),
        )

    context = repository.memory_context("NVDA", "stock")
    prompt = context.prompt_text()

    assert len(context.items) == 8
    assert len(prompt) <= 12_000
    assert all(len(item.prompt_text()) <= 2_000 for item in context.items)


def test_memory_context_skips_malformed_decisions_and_empty_reflections(
    repository: RunRepository,
) -> None:
    malformed_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Should be excluded with its malformed decision.",
        thesis="Original valid decision.",
    )
    empty_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 2),
        reflection="Will become empty.",
        thesis="Still valid.",
    )
    with repository.sessions.begin() as session:
        malformed = session.scalar(
            select(DecisionRecord).where(DecisionRecord.run_id == malformed_run_id)
        )
        empty = session.scalar(
            select(ReflectionRecord)
            .join(
                OutcomeRecord,
                OutcomeRecord.id == ReflectionRecord.outcome_id,
            )
            .join(
                DecisionRecord,
                DecisionRecord.id == OutcomeRecord.decision_id,
            )
            .where(DecisionRecord.run_id == empty_run_id)
        )
        assert malformed is not None
        malformed.decision_json = {"rating": "not-a-rating"}
        assert empty is not None
        empty.text = "   "

    assert repository.memory_context("NVDA", "stock").items == ()


def test_review_entries_support_fuzzy_filters_and_full_field_search(
    repository: RunRepository,
) -> None:
    nvda_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Valuation lesson: demand quality mattered.",
        thesis="Data center demand is accelerating.",
        rating=ResearchRating.OVERWEIGHT,
        catalysts=("Next-generation accelerator launch",),
        risks=("Power supply constraints",),
        invalidation_conditions=("Backlog contracts materially",),
        time_horizon="Three-year compound horizon",
    )
    repository.set_instrument_name(nvda_run_id, "NVIDIA")
    repository.set_instrument_local_name(nvda_run_id, "英伟达")
    _seed_memory(
        repository,
        ticker="AAPL",
        analysis_date=date(2026, 7, 2),
        reflection="Margin durability was underestimated.",
        thesis="Services growth supports margins.",
    )
    _seed_memory(
        repository,
        ticker="7203.T",
        analysis_date=date(2026, 7, 3),
        reflection="Japan-specific currency lesson.",
        thesis="Hybrid demand remains resilient.",
    )
    _seed_memory(
        repository,
        ticker="MSFT",
        analysis_date=date(2026, 7, 4),
        reflection="Pending cloud lesson.",
        thesis="Cloud growth needs confirmation.",
        resolved=False,
    )

    assert [entry["ticker"] for entry in repository.review_entries()] == [
        "MSFT",
        "7203.T",
        "AAPL",
        "NVDA",
    ]

    assert [
        entry["ticker"] for entry in repository.review_entries(ticker="vd")
    ] == ["NVDA"]
    assert {
        entry["ticker"]
        for entry in repository.review_entries(market="america/new")
    } == {"NVDA", "AAPL", "MSFT"}
    assert [
        entry["ticker"]
        for entry in repository.review_entries(q="DATA CENTER")
    ] == ["NVDA"]
    by_name = repository.review_entries(q="nvidia")
    assert [entry["ticker"] for entry in by_name] == ["NVDA"]
    assert by_name[0]["instrument_name"] == "NVIDIA"
    assert by_name[0]["instrument_local_name"] == "英伟达"
    assert by_name[0]["profile"] is RunProfile.STANDARD
    run_page = repository.list_runs(q="英伟达")
    assert run_page.items[0].research_rating is ResearchRating.OVERWEIGHT
    assert [
        entry["ticker"]
        for entry in repository.review_entries(q="valuation LESSON")
    ] == ["NVDA"]
    for decision_query in (
        "overweight",
        "accelerator launch",
        "power supply",
        "backlog contracts",
        "three-year compound",
        "nvda fixture bull scenario outcome",
    ):
        assert [
            entry["ticker"]
            for entry in repository.review_entries(q=decision_query)
        ] == ["NVDA"]
    assert [
        entry["ticker"]
        for entry in repository.review_entries(q=nvda_run_id[:12])
    ] == ["NVDA"]
    assert [
        entry["ticker"]
        for entry in repository.review_entries(q="asia/tokyo")
    ] == ["7203.T"]
    assert [
        entry["ticker"]
        for entry in repository.review_entries(status_group="in_progress")
    ] == ["MSFT"]
    assert repository.review_entries(q="pending cloud", status_group="feedback_available") == []
    assert repository.review_entries(q="%") == []


def test_review_entries_applies_derived_status_filter_before_limit(
    repository: RunRepository,
) -> None:
    available_run_id = _seed_memory(
        repository,
        ticker="NVDA",
        analysis_date=date(2026, 7, 1),
        reflection="Method lesson: Keep the older qualifying Review visible.",
        thesis="Older Review with available Feedback.",
    )
    _seed_memory(
        repository,
        ticker="MSFT",
        analysis_date=date(2026, 7, 2),
        reflection="Unused pending Reflection.",
        thesis="Newer Review still in progress.",
        resolved=False,
    )
    _outcome_id, feedback_id = _feedback_for_run(repository, available_run_id)
    with repository.sessions.begin() as session:
        feedback = session.get(OutcomeFeedbackRecord, feedback_id)
        assert feedback is not None
        feedback.status = "eligible"

    reviews = repository.review_entries(
        status_group="feedback_available",
        limit=1,
    )

    assert [review["ticker"] for review in reviews] == ["NVDA"]
