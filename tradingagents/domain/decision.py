"""Decision contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from tradingagents.domain.common import (
    FrozenModel,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
    RiskReviewDisposition,
    ScenarioReferenceCategory,
    _field_value,
    _StableStrEnum,
    _unique_evidence_refs,
)
from tradingagents.domain.evidence import MeasurementKind


class MarketReferenceBasis(_StableStrEnum):
    OBSERVED = "observed"
    INTERPRETED = "interpreted"
    DERIVED = "derived"


class NumericTemporalBasis(_StableStrEnum):
    """Temporal scope of the supporting evidence, not a forecast date."""

    POINT_IN_TIME = "point_in_time"
    LIVE_SNAPSHOT = "live_snapshot"


class EvidenceValueLocator(FrozenModel):
    """Exact Evidence Ledger location for a directly observed scalar."""

    evidence_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    table_id: str | None = Field(default=None, pattern=r"^et_[a-f0-9]{12}$")
    row_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    column: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def validate_table_location(self) -> EvidenceValueLocator:
        table_parts = (self.table_id, self.row_id, self.column)
        if any(part is not None for part in table_parts) and not all(
            part is not None for part in table_parts
        ):
            raise ValueError("table-backed evidence values require table_id, row_id, and column")
        return self


class ReferenceRangeEndpoint(FrozenModel):
    """One evidence-backed endpoint of a scenario or valuation range."""

    value: float = Field(allow_inf_nan=False, strict=True)
    basis: MarketReferenceBasis
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    source_locator: EvidenceValueLocator | None = None
    as_of_date: date
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_basis(self) -> ReferenceRangeEndpoint:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in endpoint refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed endpoint requires an Evidence locator")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("observed endpoint refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None:
                raise ValueError("interpreted endpoint must not claim an observed locator")
        elif self.basis is MarketReferenceBasis.DERIVED:
            if self.source_locator is not None:
                raise ValueError("derived endpoint must not claim an observed locator")
        return self


class ScenarioReferenceRange(FrozenModel):
    """A scenario-specific reference band, not necessarily a valuation."""

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: ReferenceRangeEndpoint
    high: ReferenceRangeEndpoint
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    interpretation: str = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> ScenarioReferenceRange:
        if self.high.value <= self.low.value:
            raise ValueError("reference range high must be greater than low")
        return self


class ResearchScenario(FrozenModel):
    kind: ResearchScenarioKind
    core_assumptions: tuple[str, ...] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    reference_ranges: tuple[ScenarioReferenceRange, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class MarketReferenceLevel(FrozenModel):
    label: str = Field(min_length=1, max_length=120)
    value: float = Field(allow_inf_nan=False, strict=True)
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    as_of_date: date
    interpretation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    basis: MarketReferenceBasis = MarketReferenceBasis.OBSERVED
    source_locator: EvidenceValueLocator | None = None
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_basis(self) -> MarketReferenceLevel:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in market reference refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed market reference requires an Evidence locator")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("market reference refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None:
                raise ValueError("interpreted market reference cannot claim an observed locator")
        elif self.basis is MarketReferenceBasis.DERIVED:
            if self.source_locator is not None:
                raise ValueError("derived market reference cannot claim a locator")
        return self


class RiskReviewAdjustment(FrozenModel):
    source_role: Literal[
        "integrated",
        "aggressive",
        "neutral",
        "conservative",
    ]
    disposition: RiskReviewDisposition
    subject: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class ResearchDecision(FrozenModel):
    """Research-only conclusion; deliberately excludes account-level advice."""

    rating: ResearchRating
    confidence: ResearchConfidenceLevel
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    unresolved_questions: tuple[str, ...] = ()
    time_horizon: str = Field(min_length=1)
    scenarios: tuple[ResearchScenario, ...] = Field(
        min_length=3,
        max_length=3,
    )
    market_reference_levels: tuple[MarketReferenceLevel, ...] = ()
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def merge_nested_evidence_refs(cls, value: Any) -> Any:
        """Make the top-level evidence index a deterministic nested-ref union."""
        if not isinstance(value, dict):
            return value
        # Retained pre-redesign Decisions may still contain ``memory_refs``.
        # Drop that retired field while hydrating the current core contract so
        # Execution History remains readable without exposing Memory again.
        value = {key: item for key, item in value.items() if key != "memory_refs"}
        merged = list(value.get("evidence_refs") or ())
        for scenario in value.get("scenarios") or ():
            merged.extend(_field_value(scenario, "evidence_refs") or ())
            for reference_range in _field_value(scenario, "reference_ranges") or ():
                for endpoint_name in ("low", "high"):
                    endpoint = _field_value(reference_range, endpoint_name)
                    merged.extend(_field_value(endpoint, "evidence_refs") or ())
        for level in value.get("market_reference_levels") or ():
            merged.extend(_field_value(level, "evidence_refs") or ())
        for adjustment in value.get("risk_review_adjustments") or ():
            merged.extend(_field_value(adjustment, "evidence_refs") or ())
        return {**value, "evidence_refs": tuple(dict.fromkeys(merged))}

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_scenario_set(self) -> ResearchDecision:
        scenario_kinds = tuple(item.kind for item in self.scenarios)
        if len(set(scenario_kinds)) != len(scenario_kinds):
            raise PydanticCustomError(
                "decision_scenarios_duplicate_kind",
                "research scenario kinds must be unique",
            )
        if set(scenario_kinds) != set(ResearchScenarioKind):
            raise PydanticCustomError(
                "decision_scenarios_incomplete_set",
                "research decision requires base, bull, and bear scenarios",
            )
        return self
