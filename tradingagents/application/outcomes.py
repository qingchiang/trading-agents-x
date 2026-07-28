"""Background settlement of five-interval decision outcomes."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import yfinance as yf

from tradingagents.dataflows.symbol_utils import market_today, normalize_symbol

from .llms import create_run_llms
from .reflection import OutcomeReflector
from .repository import RunRepository
from .settings import AppSettings

logger = logging.getLogger(__name__)


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
    ):
        self.settings = settings
        self.repository = repository
        self.history_provider = history_provider
        self._reflector = reflector

    def settle_once(self, *, limit: int = 20) -> dict[str, int]:
        stats = {"checked": 0, "resolved": 0, "pending": 0, "failed": 0}
        for item in self.repository.pending_outcomes(limit):
            stats["checked"] += 1
            try:
                observation = self.observe(
                    item["ticker"],
                    item["analysis_date"],
                    benchmark=item["benchmark"],
                    holding_intervals=item["holding_intervals"],
                )
                if observation is None:
                    self.repository.mark_outcome_checked(item["outcome_id"])
                    stats["pending"] += 1
                    continue
                reflection = self._reflection(
                    ticker=item["ticker"],
                    benchmark=item["benchmark"],
                    decision=item["decision"],
                    observation=observation,
                )
                self.repository.resolve_outcome(
                    item["outcome_id"],
                    observation_start=observation.start_date,
                    observation_end=observation.end_date,
                    raw_return=observation.raw_return,
                    alpha_return=observation.alpha_return,
                    reflection=reflection,
                )
                stats["resolved"] += 1
            except Exception as exc:
                logger.warning(
                    "Outcome settlement failed for %s: %s",
                    item["ticker"],
                    exc,
                )
                self.repository.mark_outcome_checked(
                    item["outcome_id"],
                    type(exc).__name__,
                )
                stats["failed"] += 1
        return stats

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
        )
        bench = self.history_provider.Ticker(benchmark).history(
            start=analysis_date.isoformat(),
            end=benchmark_end.isoformat(),
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
                output_language=(
                    self.settings.default_run_settings.output_language.prompt_label
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
