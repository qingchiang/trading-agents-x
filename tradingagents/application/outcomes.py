"""Background settlement of five-interval decision outcomes."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from tradingagents.dataflows.symbol_utils import market_today, normalize_symbol

from .contracts import report_language_prompt_label
from .llms import create_run_llms
from .reflection import OutcomeReflector
from .repository import RunRepository
from .settings import AppSettings

logger = logging.getLogger(__name__)

PENDING_RECHECK_INTERVAL = timedelta(hours=24)
ERROR_RECHECK_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class OutcomeObservation:
    raw_return: float
    alpha_return: float
    holding_intervals: int
    start_date: date
    end_date: date


def close_by_local_date(frame: pd.DataFrame) -> pd.Series:
    """Keep the exchange-local calendar labels supplied by each history."""
    if frame.empty or "Close" not in frame:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    closes = pd.Series(
        frame["Close"].to_numpy(),
        index=index.normalize(),
        dtype=float,
    )
    return closes[~closes.index.duplicated(keep="last")].dropna().sort_index()


class OutcomeSettlement:
    def __init__(
        self,
        settings: AppSettings,
        repository: RunRepository,
        *,
        history_provider: Any = yf,
        reflector: OutcomeReflector | None = None,
        utc_clock: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.history_provider = history_provider
        self._reflector = reflector
        self.utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))

    def settle_once(self, *, limit: int = 20) -> dict[str, int]:
        stats = {"checked": 0, "resolved": 0, "pending": 0, "failed": 0}
        now = self.utc_clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        for item in self.repository.pending_outcomes(limit, due_at=now):
            stats["checked"] += 1
            observation = self._persisted_observation(item)
            if observation is None:
                try:
                    observation = self.observe(
                        item["ticker"],
                        item["analysis_date"],
                        benchmark=item["benchmark"],
                        holding_intervals=item["holding_intervals"],
                    )
                except Exception as exc:
                    logger.warning(
                        "Outcome observation failed for %s: %s",
                        item["ticker"],
                        type(exc).__name__,
                    )
                    self.repository.mark_outcome_checked(
                        item["outcome_id"],
                        checked_at=now,
                        next_check_at=now + ERROR_RECHECK_INTERVAL,
                        error_message=type(exc).__name__,
                    )
                    stats["failed"] += 1
                    continue
                if observation is None:
                    self.repository.mark_outcome_checked(
                        item["outcome_id"],
                        checked_at=now,
                        next_check_at=now + PENDING_RECHECK_INTERVAL,
                    )
                    stats["pending"] += 1
                    continue
                self.repository.persist_outcome_observation(
                    item["outcome_id"],
                    observation=observation,
                    observed_at=now,
                )
            try:
                reflection = self._reflection(
                    ticker=item["ticker"],
                    benchmark=item["benchmark"],
                    decision=item["decision"],
                    observation=observation,
                )
                reflection_status = self.repository.persist_generated_reflection(
                    item["outcome_id"],
                    reflection=reflection,
                    generated_at=now,
                )
                stats["failed" if reflection_status == "invalid" else "resolved"] += 1
            except Exception as exc:
                logger.warning(
                    "Outcome reflection failed for %s: %s",
                    item["ticker"],
                    type(exc).__name__,
                )
                self.repository.mark_reflection_failure(
                    item["outcome_id"],
                    attempted_at=now,
                    next_retry_at=now + ERROR_RECHECK_INTERVAL,
                    error_code=type(exc).__name__,
                )
                stats["failed"] += 1
        return stats

    @staticmethod
    def _persisted_observation(item: dict[str, Any]) -> OutcomeObservation | None:
        required = (
            item.get("observation_start"),
            item.get("observation_end"),
            item.get("raw_return"),
            item.get("alpha_return"),
        )
        if any(value is None for value in required):
            return None
        return OutcomeObservation(
            raw_return=float(item["raw_return"]),
            alpha_return=float(item["alpha_return"]),
            holding_intervals=int(item["holding_intervals"]),
            start_date=item["observation_start"],
            end_date=item["observation_end"],
        )

    def observe(
        self,
        ticker: str,
        analysis_date: date,
        *,
        benchmark: str,
        holding_intervals: int = 5,
    ) -> OutcomeObservation | None:
        canonical = normalize_symbol(ticker)
        benchmark = normalize_symbol(benchmark)
        stock_end = market_today(canonical)
        benchmark_end = market_today(benchmark)
        if stock_end <= analysis_date or benchmark_end <= analysis_date:
            return None
        stock = self.history_provider.Ticker(canonical).history(
            start=analysis_date.isoformat(),
            end=stock_end.isoformat(),
            auto_adjust=True,
            actions=False,
        )
        bench = self.history_provider.Ticker(benchmark).history(
            start=analysis_date.isoformat(),
            end=benchmark_end.isoformat(),
            auto_adjust=True,
            actions=False,
        )
        stock_close = close_by_local_date(stock)
        benchmark_close = close_by_local_date(bench)
        common = stock_close.index.intersection(benchmark_close.index).sort_values()
        required = holding_intervals + 1
        if len(common) < required:
            return None
        start = common[0]
        end = common[holding_intervals]
        raw = float(stock_close.loc[end] / stock_close.loc[start] - 1)
        benchmark_return = float(
            benchmark_close.loc[end] / benchmark_close.loc[start] - 1
        )
        return OutcomeObservation(
            raw_return=raw,
            alpha_return=raw - benchmark_return,
            holding_intervals=holding_intervals,
            start_date=start.date(),
            end_date=end.date(),
        )

    def _reflection(
        self,
        *,
        ticker: str,
        benchmark: str,
        decision: dict[str, Any],
        observation: OutcomeObservation,
    ) -> str:
        reflector = self._reflector
        if reflector is None:
            quick, _ = create_run_llms(self.settings.default_run_settings)
            reflector = OutcomeReflector(
                quick,
                output_language=report_language_prompt_label(
                    self.settings.default_run_settings.output_language
                ),
            )
            self._reflector = reflector
        return reflector.reflect(
            decision=json.dumps(decision, ensure_ascii=False),
            raw_return=observation.raw_return,
            alpha_return=observation.alpha_return,
            benchmark=benchmark,
            ticker=ticker,
            holding_intervals=observation.holding_intervals,
            observation_start=observation.start_date.isoformat(),
            observation_end=observation.end_date.isoformat(),
        )
