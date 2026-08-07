from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from tradingagents.application.outcome_schedule import earliest_outcome_check_at
from tradingagents.application.outcomes import (
    ERROR_RECHECK_INTERVAL,
    PENDING_RECHECK_INTERVAL,
    OutcomeSettlement,
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
