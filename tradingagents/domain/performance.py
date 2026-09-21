"""Performance contracts."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from tradingagents.domain.collection import CollectionDiagnostic
from tradingagents.domain.common import FrozenModel, _StableStrEnum


class MarketSeriesPoint(FrozenModel):
    session: date
    completed_at: datetime
    adjusted_close: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_completed_at(self) -> MarketSeriesPoint:
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("market-series completed_at must include a timezone")
        return self


class MarketSeriesResult(FrozenModel):
    """One completed-session series from a single provider and retrieval vintage."""

    instrument: str = Field(min_length=1)
    source: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    adjustment_basis: str = Field(min_length=1, max_length=120)
    retrieved_at: datetime
    fallback: bool = False
    points: tuple[MarketSeriesPoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_series(self) -> MarketSeriesResult:
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("market-series retrieved_at must include a timezone")
        sessions = tuple(point.session for point in self.points)
        if sessions != tuple(sorted(set(sessions))):
            raise ValueError("market-series sessions must be unique and ordered")
        completions = tuple(point.completed_at for point in self.points)
        if completions != tuple(sorted(completions)):
            raise ValueError("market-series completion times must be ordered")
        if any(completed_at > self.retrieved_at for completed_at in completions):
            raise ValueError("market-series sessions must complete before retrieval")
        return self


class BenchmarkSeriesResult(FrozenModel):
    """One benchmark's collected series or truthful unavailability."""

    name: str = Field(min_length=1, max_length=120)
    series: MarketSeriesResult | None = None
    unavailable_diagnostic: CollectionDiagnostic | None = None

    @model_validator(mode="after")
    def validate_result_state(self) -> BenchmarkSeriesResult:
        if (self.series is None) == (self.unavailable_diagnostic is None):
            raise ValueError(
                "benchmark collection requires either a series or an unavailable diagnostic"
            )
        return self


class PerformanceComponentStatus(_StableStrEnum):
    CALCULATED = "calculated"
    NOT_YET_OBSERVABLE = "not_yet_observable"
    UNAVAILABLE = "unavailable"


class PerformanceCalculationRecord(FrozenModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    fallback: bool = False
    adjustment_basis: str = Field(min_length=1, max_length=120)
    retrieved_at: datetime
    baseline_information_cutoff_at: datetime
    target_information_cutoff_at: datetime
    start_session: date
    end_session: date
    start_value: float = Field(gt=0, allow_inf_nan=False)
    end_value: float = Field(gt=0, allow_inf_nan=False)
    formula: Literal["(end_value / start_value) - 1"] = "(end_value / start_value) - 1"
    unrounded_return: float = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_calculation_boundary(self) -> PerformanceCalculationRecord:
        for field_name in (
            "retrieved_at",
            "baseline_information_cutoff_at",
            "target_information_cutoff_at",
        ):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a timezone")
        if self.baseline_information_cutoff_at >= self.target_information_cutoff_at:
            raise ValueError("Performance information cutoffs must advance")
        if self.start_session >= self.end_session:
            raise ValueError("calculated Performance sessions must advance")
        expected_return = (self.end_value / self.start_value) - 1
        if not math.isclose(
            self.unrounded_return,
            expected_return,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise ValueError("unrounded return must match endpoint values")
        return self


class PerformanceComponent(FrozenModel):
    status: PerformanceComponentStatus
    reason: str | None = Field(default=None, min_length=1)
    calculation: PerformanceCalculationRecord | None = None

    @model_validator(mode="after")
    def validate_component_state(self) -> PerformanceComponent:
        if self.status is PerformanceComponentStatus.CALCULATED:
            if self.calculation is None:
                raise ValueError("calculated Performance requires a calculation record")
        elif self.calculation is not None or self.reason is None:
            raise ValueError(
                "non-calculated Performance requires a reason and no calculation record"
            )
        return self


class BenchmarkContext(FrozenModel):
    name: str = Field(min_length=1, max_length=120)
    component: PerformanceComponent
    reported_difference: float | None = Field(default=None, allow_inf_nan=False)


class PerformanceObservation(FrozenModel):
    stock: PerformanceComponent
    benchmarks: tuple[BenchmarkContext, ...] = ()

    @model_validator(mode="after")
    def validate_unique_benchmarks(self) -> PerformanceObservation:
        names = tuple(item.name for item in self.benchmarks)
        if len(names) != len(set(names)):
            raise ValueError("Benchmark Context names must be unique")
        return self
