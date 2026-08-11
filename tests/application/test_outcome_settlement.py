from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from tradingagents.application.outcome_schedule import earliest_outcome_check_at
from tradingagents.application.outcomes import (
    ERROR_RECHECK_INTERVAL,
    PENDING_RECHECK_INTERVAL,
    OutcomeObservation,
    OutcomeSettlement,
)
from tradingagents.application.reflection import (
    OutcomeReflectionDraft,
    ReflectionDraftValidationError,
)


def _draft() -> OutcomeReflectionDraft:
    return OutcomeReflectionDraft(
        directional_assessment="mixed",
        source_decision_evidence_lesson="Compare the stored decision evidence.",
        method_lesson="Use explicit short-window checks when reviewing methodology.",
    )


class _Ticker:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def history(self, **_kwargs):
        return self.frame


class _History:
    def __init__(self, frames):
        self.frames = frames

    def Ticker(self, ticker):
        return _Ticker(self.frames[ticker])


def _frame(dates, closes):
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(dates))


class _ScheduledRepository:
    def __init__(self, item):
        self.item = item
        self.due_at = None
        self.due_at = None
        self.checked = []

    def pending_outcomes(self, limit, *, due_at):
        self.due_at = due_at
        return [self.item]

    def mark_outcome_checked(self, outcome_id, **kwargs):
        self.checked.append((outcome_id, kwargs))


def _pending_item():
    return {
        "outcome_id": 7,
        "ticker": "6501.T",
        "analysis_date": date(2026, 7, 28),
        "benchmark": "^N225",
        "holding_intervals": 5,
        "decision": {},
    }


def test_earliest_check_uses_six_market_closes_and_local_timezone() -> None:
    stock_due = earliest_outcome_check_at(
        ticker="6501.T",
        analysis_date=date(2026, 7, 28),
        holding_intervals=5,
    )

    assert stock_due == datetime(2026, 8, 4, 15, tzinfo=timezone.utc)


def test_pending_observation_is_deferred_for_24_hours(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    repository = _ScheduledRepository(_pending_item())
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        utc_clock=lambda: now,
    )
    monkeypatch.setattr(settlement, "observe", lambda *_args, **_kwargs: None)

    stats = settlement.settle_once()

    assert stats == {"checked": 1, "resolved": 0, "pending": 1, "failed": 0}
    assert repository.due_at == now
    assert repository.checked == [
        (
            7,
            {
                "checked_at": now,
                "next_check_at": now + PENDING_RECHECK_INTERVAL,
            },
        )
    ]


def test_provider_failure_is_deferred_for_one_hour(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    repository = _ScheduledRepository(_pending_item())
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        utc_clock=lambda: now,
    )

    def fail(*_args, **_kwargs):
        raise ConnectionError("fixture secret must not persist")

    monkeypatch.setattr(settlement, "observe", fail)

    stats = settlement.settle_once()

    assert stats == {"checked": 1, "resolved": 0, "pending": 0, "failed": 1}
    assert repository.checked == [
        (
            7,
            {
                "checked_at": now,
                "next_check_at": now + ERROR_RECHECK_INTERVAL,
                "error_message": "ConnectionError",
            },
        )
    ]


class _LifecycleRepository:
    def __init__(self, item):
        self.item = item
        self.observations = []
        self.failures = []
        self.reflections = []

    def pending_outcomes(self, _limit, *, due_at):
        self.due_at = due_at
        if self.item["reflection_status"] == "generated":
            return []
        return [dict(self.item)]

    def persist_outcome_observation(self, outcome_id, *, observation, observed_at):
        self.observations.append((outcome_id, observation, observed_at))
        self.item.update(
            {
                "status": "resolved",
                "observation_start": observation.start_date,
                "observation_end": observation.end_date,
                "raw_return": observation.raw_return,
                "alpha_return": observation.alpha_return,
                "reflection_status": "pending",
            }
        )

    def start_outcome_reflection_attempt(self, outcome_id, *, started_at):
        self.started_attempt = (outcome_id, started_at)
        return {"cycle_id": "fixture-cycle", "attempt_id": 1}

    def start_outcome_reflection_repair_attempt(self, outcome_id, *, attempt_ids, started_at):
        self.repair_attempt = (outcome_id, attempt_ids, started_at)
        return {"cycle_id": "fixture-cycle", "attempt_id": 2}

    def mark_reflection_failure(self, outcome_id, **kwargs):
        self.failures.append((outcome_id, kwargs))
        self.item["reflection_status"] = "retryable_failure"

    def persist_generated_reflection(self, outcome_id, **kwargs):
        self.reflections.append((outcome_id, kwargs))
        status = "generated" if kwargs.get("draft") is not None else "invalid"
        if kwargs.get("terminal_invalid", True):
            self.item["reflection_status"] = status
        return status


def test_observation_survives_reflection_failure_and_retry_does_not_reobserve(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    item = {
        **_pending_item(),
        "status": "pending",
        "reflection_status": None,
        "observation_start": None,
        "observation_end": None,
        "raw_return": None,
        "alpha_return": None,
        "market_timezone": "Asia/Tokyo",
    }
    repository = _LifecycleRepository(item)
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        reflector=object(),
        utc_clock=lambda: now,
    )
    observation = OutcomeObservation(
        raw_return=0.10,
        alpha_return=0.04,
        holding_intervals=5,
        start_date=date(2026, 7, 29),
        end_date=date(2026, 8, 5),
    )
    observe_calls = 0
    reflection_calls = 0

    def observe(*_args, **_kwargs):
        nonlocal observe_calls
        observe_calls += 1
        return observation

    def reflect(**_kwargs):
        nonlocal reflection_calls
        reflection_calls += 1
        if reflection_calls == 1:
            raise RuntimeError("sensitive provider detail")
        return _draft()

    monkeypatch.setattr(settlement, "observe", observe)
    monkeypatch.setattr(settlement, "_reflection", reflect)

    first = settlement.settle_once()
    second = settlement.settle_once()

    assert first == {"checked": 1, "resolved": 0, "pending": 0, "failed": 1}
    assert second == {"checked": 1, "resolved": 1, "pending": 0, "failed": 0}
    assert observe_calls == 1
    assert len(repository.observations) == 1
    assert repository.failures[0][1]["error_code"] == "RuntimeError"
    assert repository.reflections[0][1]["draft"].method_lesson.startswith("Use explicit")


def test_invalid_reflection_does_not_recompute_completed_observation(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    item = {
        **_pending_item(),
        "status": "resolved",
        "reflection_status": "pending",
        "observation_start": date(2026, 7, 29),
        "observation_end": date(2026, 8, 5),
        "raw_return": 0.10,
        "alpha_return": 0.04,
    }
    repository = _LifecycleRepository(item)
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        reflector=object(),
        utc_clock=lambda: now,
    )
    monkeypatch.setattr(
        settlement,
        "observe",
        lambda *_args, **_kwargs: pytest.fail("Observation was recomputed"),
    )
    invalid = ReflectionDraftValidationError(
        candidate="{}",
        validation_issues=("missing:directional_assessment",),
        candidate_digest="digest-1",
        candidate_length=5_001,
    )
    monkeypatch.setattr(settlement, "_reflection", lambda **_kwargs: (_ for _ in ()).throw(invalid))
    monkeypatch.setattr(settlement, "_repair_reflection", lambda **_kwargs: (_ for _ in ()).throw(invalid))

    stats = settlement.settle_once()

    assert stats == {"checked": 1, "resolved": 0, "pending": 0, "failed": 1}
    assert repository.observations == []
    assert repository.item["reflection_status"] == "invalid"
    assert repository.repair_attempt[1] == {"cycle_id": "fixture-cycle", "attempt_id": 1}
    assert [entry[1]["attempt_ids"]["attempt_id"] for entry in repository.reflections] == [
        1,
        2,
    ]
    assert repository.reflections[0][1]["invalid_candidate_digest"] == "digest-1"
    assert repository.reflections[0][1]["invalid_candidate_length"] == 5_001


def test_invalid_initial_draft_is_repaired_once_without_reobserving(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    item = {
        **_pending_item(),
        "status": "resolved",
        "reflection_status": "pending",
        "observation_start": date(2026, 7, 29),
        "observation_end": date(2026, 8, 5),
        "raw_return": 0.10,
        "alpha_return": 0.04,
    }
    repository = _LifecycleRepository(item)
    settlement = OutcomeSettlement(
        app_settings, repository, reflector=object(), utc_clock=lambda: now
    )
    invalid = ReflectionDraftValidationError(
        candidate='<script>bad</script>', validation_issues=("method_lesson",)
    )
    monkeypatch.setattr(
        settlement,
        "observe",
        lambda *_args, **_kwargs: pytest.fail("Observation was recomputed"),
    )
    monkeypatch.setattr(
        settlement, "_reflection", lambda **_kwargs: (_ for _ in ()).throw(invalid)
    )
    monkeypatch.setattr(settlement, "_repair_reflection", lambda **_kwargs: _draft())

    assert settlement.settle_once() == {
        "checked": 1,
        "resolved": 1,
        "pending": 0,
        "failed": 0,
    }
    assert repository.item["reflection_status"] == "generated"
    assert [entry[1]["attempt_ids"]["attempt_id"] for entry in repository.reflections] == [
        1,
        2,
    ]


def test_schema_invalid_cycle_does_not_schedule_provider_retry_after_repair_failure(
    app_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    item = {
        **_pending_item(),
        "status": "resolved",
        "reflection_status": "pending",
        "observation_start": date(2026, 7, 29),
        "observation_end": date(2026, 8, 5),
        "raw_return": 0.10,
        "alpha_return": 0.04,
    }
    repository = _LifecycleRepository(item)
    settlement = OutcomeSettlement(
        app_settings, repository, reflector=object(), utc_clock=lambda: now
    )
    invalid = ReflectionDraftValidationError(
        candidate="{}", validation_issues=("method_lesson",)
    )
    monkeypatch.setattr(
        settlement, "_reflection", lambda **_kwargs: (_ for _ in ()).throw(invalid)
    )
    monkeypatch.setattr(
        settlement,
        "_repair_reflection",
        lambda **_kwargs: (_ for _ in ()).throw(ConnectionError("provider unavailable")),
    )

    assert settlement.settle_once() == {
        "checked": 1,
        "resolved": 0,
        "pending": 0,
        "failed": 1,
    }
    assert repository.failures == []
    assert repository.item["reflection_status"] == "invalid"
    assert repository.reflections[-1][1]["validation_issues"] == [
        "repair_provider_failure",
        "ConnectionError",
    ]


def test_lifecycle_timestamps_are_captured_after_each_phase(
    app_settings,
    monkeypatch,
) -> None:
    due_at = datetime(2026, 8, 5, 0, tzinfo=timezone.utc)
    observed_at = datetime(2026, 8, 5, 0, 1, tzinfo=timezone.utc)
    generated_at = datetime(2026, 8, 5, 0, 3, tzinfo=timezone.utc)
    clock = iter((due_at, observed_at, generated_at))
    item = {
        **_pending_item(),
        "status": "pending",
        "reflection_status": None,
        "observation_start": None,
        "observation_end": None,
        "raw_return": None,
        "alpha_return": None,
    }
    repository = _LifecycleRepository(item)
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        reflector=object(),
        utc_clock=lambda: next(clock),
    )
    monkeypatch.setattr(
        settlement,
        "observe",
        lambda *_args, **_kwargs: OutcomeObservation(
            raw_return=0.10,
            alpha_return=0.04,
            holding_intervals=5,
            start_date=date(2026, 7, 29),
            end_date=date(2026, 8, 5),
        ),
    )
    monkeypatch.setattr(
        settlement,
        "_reflection",
        lambda **_kwargs: _draft(),
    )

    settlement.settle_once()

    assert repository.due_at == due_at
    assert repository.observations[0][2] == observed_at
    assert repository.reflections[0][1]["generated_at"] == generated_at


def test_observe_requires_six_common_closes_for_five_intervals(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tradingagents.application.outcomes.market_today",
        lambda _ticker: date(2026, 7, 31),
    )
    history = _History(
        {
            "7203.T": _frame(
                [
                    "2026-07-21",
                    "2026-07-22",
                    "2026-07-23",
                    "2026-07-24",
                    "2026-07-27",
                    "2026-07-28",
                ],
                [100, 101, 102, 103, 104, 110],
            ),
            "^N225": _frame(
                [
                    "2026-07-21",
                    "2026-07-22",
                    "2026-07-23",
                    "2026-07-24",
                    "2026-07-27",
                    "2026-07-28",
                ],
                [200, 202, 204, 206, 208, 210],
            ),
        }
    )
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        history_provider=history,
    )

    observation = settlement.observe(
        "7203.T",
        date(2026, 7, 20),
        benchmark="^N225",
    )

    assert observation is not None
    assert observation.holding_intervals == 5
    assert observation.start_date == date(2026, 7, 21)
    assert observation.end_date == date(2026, 7, 28)
    assert observation.raw_return == pytest.approx(0.1)
    assert observation.alpha_return == pytest.approx(0.05)


def test_observe_remains_pending_with_only_five_common_closes(
    app_settings,
    repository,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tradingagents.application.outcomes.market_today",
        lambda _ticker: date(2026, 7, 31),
    )
    frame = _frame(
        [
            "2026-07-21",
            "2026-07-22",
            "2026-07-23",
            "2026-07-24",
            "2026-07-27",
        ],
        [100, 101, 102, 103, 104],
    )
    settlement = OutcomeSettlement(
        app_settings,
        repository,
        history_provider=_History({"NVDA": frame, "SPY": frame}),
    )

    assert (
        settlement.observe(
            "NVDA",
            date(2026, 7, 20),
            benchmark="SPY",
        )
        is None
    )
