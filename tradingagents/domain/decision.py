"""Decision contracts."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from tradingagents.domain.common import (
    _DECISION_COMPONENT_PATH_PATTERN,
    _RESEARCH_ID_PATTERN,
    FrozenModel,
    NumericAuditStatus,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
    RiskReviewDisposition,
    ScenarioReferenceCategory,
    _field_value,
    _StableStrEnum,
    _unique_evidence_refs,
    _unique_research_ids,
)
from tradingagents.domain.evidence import MeasurementKind
from tradingagents.domain.numeric_audit import MarketReferenceBasis


class NumericTemporalBasis(_StableStrEnum):
    """How the application determined the date of a formal numeric value."""

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


class AuditedRangeEndpoint(FrozenModel):
    """One evidence-backed endpoint of a scenario or valuation range."""

    value: float
    basis: MarketReferenceBasis
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    source_locator: EvidenceValueLocator | None = None
    calculation_id: str | None = None
    as_of_date: date
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("calculation_id")
    @classmethod
    def validate_calculation_id(cls, value: str | None) -> str | None:
        if value is not None and not _RESEARCH_ID_PATTERN.fullmatch(value):
            raise ValueError("invalid calculation identifier")
        return value

    @model_validator(mode="after")
    def validate_basis(self) -> AuditedRangeEndpoint:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in endpoint refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed endpoint requires an Evidence locator")
            if self.calculation_id:
                raise ValueError("observed endpoint must not reference a calculation")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("observed endpoint refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None or self.calculation_id:
                raise ValueError("interpreted endpoint must not claim a locator or calculation")
        elif self.basis is MarketReferenceBasis.DERIVED:
            if not self.calculation_id:
                raise ValueError("derived endpoint requires a calculation")
            if self.source_locator is not None:
                raise ValueError("derived endpoint must not claim an observed locator")
        return self


class ScenarioReferenceRange(FrozenModel):
    """A scenario-specific reference band, not necessarily a valuation."""

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: AuditedRangeEndpoint
    high: AuditedRangeEndpoint
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


class ValuationAssessment(FrozenModel):
    method: str = Field(min_length=1)
    low: AuditedRangeEndpoint
    high: AuditedRangeEndpoint
    measurement_kind: MeasurementKind
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_valuation(self) -> ValuationAssessment:
        if self.low.basis is not MarketReferenceBasis.DERIVED:
            raise ValueError("valuation low endpoint must be derived")
        if self.high.basis is not MarketReferenceBasis.DERIVED:
            raise ValueError("valuation high endpoint must be derived")
        if self.measurement_kind is MeasurementKind.UNKNOWN:
            raise ValueError("valuation measurement must be known")
        if self.high.value < self.low.value:
            raise ValueError("valuation high must be >= low")
        return self

    @property
    def calculation_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item
                for item in (self.low.calculation_id, self.high.calculation_id)
                if item is not None
            )
        )

    @property
    def input_evidence_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.low.evidence_refs, *self.high.evidence_refs)))

    @property
    def as_of_date(self) -> date:
        return max(self.low.as_of_date, self.high.as_of_date)


class MarketReferenceLevel(FrozenModel):
    label: str = Field(min_length=1, max_length=120)
    value: float
    measurement_kind: MeasurementKind = MeasurementKind.UNKNOWN
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    as_of_date: date
    interpretation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = Field(min_length=1)
    basis: MarketReferenceBasis = MarketReferenceBasis.OBSERVED
    source_locator: EvidenceValueLocator | None = None
    calculation_ids: tuple[str, ...] = ()
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME

    @field_validator("evidence_refs", "date_evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("calculation_ids")
    @classmethod
    def validate_calculation_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)

    @model_validator(mode="after")
    def validate_basis(self) -> MarketReferenceLevel:
        if not set(self.date_evidence_refs).issubset(self.evidence_refs):
            raise ValueError("date evidence refs must be included in market reference refs")
        if self.basis is MarketReferenceBasis.OBSERVED:
            if self.source_locator is None:
                raise ValueError("observed market reference requires an Evidence locator")
            if self.calculation_ids:
                raise ValueError("observed market reference cannot use calculations")
            if self.source_locator.evidence_ref not in self.evidence_refs:
                raise ValueError("market reference refs must include its locator ref")
        elif self.basis is MarketReferenceBasis.INTERPRETED:
            if self.source_locator is not None or self.calculation_ids:
                raise ValueError(
                    "interpreted market reference cannot claim direct or derived audit"
                )
        elif self.basis is MarketReferenceBasis.DERIVED:
            if not self.calculation_ids:
                raise ValueError("derived market reference requires a calculation")
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


class DecisionCalculationUse(FrozenModel):
    """One readable decision component that relies on a calculation."""

    component_path: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=200)


class CalculationRecord(FrozenModel):
    """A decision-critical calculation, not a presentation-table cell."""

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: dict[str, int | float] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    date_evidence_refs: tuple[str, ...] = ()
    result: int | float
    unit: str = Field(min_length=1, max_length=32)
    as_of_date: date
    temporal_basis: NumericTemporalBasis = NumericTemporalBasis.POINT_IN_TIME
    limitations: tuple[str, ...] = Field(min_length=1)
    decision_uses: tuple[DecisionCalculationUse, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_inputs(
        cls,
        value: dict[str, int | float],
    ) -> dict[str, int | float]:
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) for key in value):
            raise ValueError("calculation input names must be identifiers")
        if any(isinstance(item, bool) for item in value.values()):
            raise ValueError("calculation inputs must be numeric")
        return value

    @field_validator("input_evidence_refs")
    @classmethod
    def validate_input_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("date_evidence_refs")
    @classmethod
    def validate_date_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_date_ref_subset(self) -> CalculationRecord:
        if not set(self.date_evidence_refs).issubset(self.input_evidence_refs):
            raise ValueError("calculation date refs must belong to input evidence refs")
        return self


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
    valuation_assessment: ValuationAssessment | None = None
    market_reference_levels: tuple[MarketReferenceLevel, ...] = ()
    calculation_records: tuple[CalculationRecord, ...] = ()
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = ()
    numeric_audit_status: NumericAuditStatus | None = None

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
        valuation = value.get("valuation_assessment")
        if valuation is not None:
            for endpoint_name in ("low", "high"):
                endpoint = _field_value(valuation, endpoint_name)
                merged.extend(_field_value(endpoint, "evidence_refs") or ())
        for level in value.get("market_reference_levels") or ():
            merged.extend(_field_value(level, "evidence_refs") or ())
        for calculation in value.get("calculation_records") or ():
            merged.extend(_field_value(calculation, "input_evidence_refs") or ())
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
