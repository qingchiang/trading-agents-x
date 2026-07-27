from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tradingagents.application.outcomes import OutcomeSettlement


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
