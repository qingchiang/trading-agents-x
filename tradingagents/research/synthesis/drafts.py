"""Serializer-facing research drafts and generated artifact outputs."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    field_serializer,
    field_validator,
    model_validator,
)

from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    NumericDisplayScale,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
    ScenarioReferenceCategory,
)
from tradingagents.domain.decision import (
    ResearchDecision,
    RiskReviewAdjustment,
)
from tradingagents.domain.numeric_audit import (
    DecisionNumericAuditAppendix,
    MarketReferenceBasis,
)
from tradingagents.domain.reports import (
    IssueDisposition,
    ResearchWarning,
)

EventWriter = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ResearchMarkdown:
    """One readable artifact body and non-fatal citation warnings."""

    markdown: str
    evidence_refs: tuple[str, ...]
    warnings: tuple[ResearchWarning, ...]


class RebuttalAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()


class JudgeAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preliminary_rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)


class CalculationInputDraft(BaseModel):
    """Serializer-facing numeric input with a schema-visible identifier."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: int | float
    date_evidence_refs: tuple[str, ...] = ()


class CalculationRecordDraft(BaseModel):
    """Serializer-facing calculation without dynamic JSON object keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)
    requirement_ids: tuple[str, ...] = ()

    @field_validator("inputs")
    @classmethod
    def validate_unique_inputs(
        cls,
        value: tuple[CalculationInputDraft, ...],
    ) -> tuple[CalculationInputDraft, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("calculation input names must be unique")
        return value

    def input_mapping(self) -> dict[str, int | float]:
        return {item.name: item.value for item in self.inputs}

    @field_validator("requirement_ids")
    @classmethod
    def validate_requirement_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        result = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"req_[a-z0-9][a-z0-9_.-]*", item) for item in result):
            raise ValueError("invalid numeric requirement identifier")
        return result


class DecisionNumericRequirementDraft(BaseModel):
    """A derived number used by one strict qualitative decision component."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^req_[a-z0-9][a-z0-9_.-]*$")
    component_path: str = Field(
        pattern=(
            r"^(?:executive_summary|thesis|catalysts\.\d+|risks\.\d+|"
            r"invalidation_conditions\.\d+|"
            r"scenarios\.(?:base|bull|bear)\."
            r"(?:outcome|core_assumptions\.\d+)|"
            r"risk_review_adjustments\.\d+\.explanation)$"
        )
    )
    label: str = Field(min_length=1, max_length=200)
    stated_value: float = Field(allow_inf_nan=False)
    fraction_digits: int = Field(ge=0, le=8)
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    display_scale: NumericDisplayScale = Field(
        description=(
            "Reader-facing scale for the formula result only; never inherit an "
            "input's measurement scale. Dimensionless %, percent, pct, pp, "
            "percentage points, bps, basis points, x, and 倍 results use base."
        )
    )
    display_role: Literal["scalar", "range_low", "range_high"] = "scalar"
    display_group_id: str | None = Field(
        default=None,
        pattern=r"^group_[a-z0-9][a-z0-9_.-]*$",
    )
    limitations: tuple[str, ...] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def validate_unique_inputs(
        cls,
        value: tuple[CalculationInputDraft, ...],
    ) -> tuple[CalculationInputDraft, ...]:
        names = tuple(item.name for item in value)
        if len(names) != len(set(names)):
            raise ValueError("numeric requirement input names must be unique")
        return value

    @model_validator(mode="after")
    def validate_display_group(self) -> DecisionNumericRequirementDraft:
        if self.display_role == "scalar" and self.display_group_id is not None:
            raise ValueError("scalar requirements cannot belong to a range group")
        if self.display_role != "scalar" and self.display_group_id is None:
            raise ValueError("range endpoint requirements require a display group")
        return self


class ResearchScenarioCoreDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResearchScenarioKind
    core_assumptions: tuple[str, ...] = Field(min_length=1)
    outcome: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ResearchDecisionCoreDraft(BaseModel):
    """Strict decision fields that must survive optional numeric failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rating: ResearchRating
    confidence: ResearchConfidenceLevel
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    unresolved_questions: tuple[str, ...] = ()
    time_horizon: str = Field(min_length=1)
    scenarios: tuple[ResearchScenarioCoreDraft, ...] = Field(
        min_length=3,
        max_length=3,
    )
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = ()


class ResearchDecisionCoreEnvelope(ResearchDecisionCoreDraft):
    """Serializer wire envelope with soft numeric annotation candidates."""

    numeric_requirements_declared: bool = False
    numeric_requirement_candidates: tuple[SkipValidation[DecisionNumericRequirementDraft], ...] = ()

    @field_serializer("numeric_requirement_candidates", mode="plain")
    def serialize_numeric_requirement_candidates(
        self,
        value: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return tuple(
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value
        )

    def qualitative_core(self) -> ResearchDecisionCoreDraft:
        return ResearchDecisionCoreDraft.model_validate(
            self.model_dump(
                exclude={
                    "numeric_requirements_declared",
                    "numeric_requirement_candidates",
                }
            )
        )


class ObservedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")


class InterpretedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.INTERPRETED] = MarketReferenceBasis.INTERPRETED
    value: float
    anchor_value_refs: tuple[str, ...] = Field(min_length=1)
    context_evidence_refs: tuple[str, ...] = ()

    @field_validator("anchor_value_refs")
    @classmethod
    def validate_anchor_value_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"nv_[a-f0-9]{12}", item) for item in refs):
            raise ValueError("invalid numeric anchor reference")
        return refs


class DerivedRangeEndpointDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    basis: Literal[MarketReferenceBasis.DERIVED] = MarketReferenceBasis.DERIVED
    calculation_id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")


type RangeEndpointDraft = Annotated[
    ObservedRangeEndpointDraft | InterpretedRangeEndpointDraft | DerivedRangeEndpointDraft,
    Field(discriminator="basis"),
]


class ScenarioReferenceRangeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ScenarioReferenceCategory
    label: str = Field(min_length=1, max_length=120)
    low: RangeEndpointDraft
    high: RangeEndpointDraft
    interpretation: str = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)


class ScenarioReferenceRangesDraft(BaseModel):
    """Fixed scenario buckets that structurally prevent duplicate kinds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base: tuple[ScenarioReferenceRangeDraft, ...] = ()
    bull: tuple[ScenarioReferenceRangeDraft, ...] = ()
    bear: tuple[ScenarioReferenceRangeDraft, ...] = ()

    def items(
        self,
    ) -> tuple[tuple[ResearchScenarioKind, tuple[ScenarioReferenceRangeDraft, ...]], ...]:
        return (
            (ResearchScenarioKind.BASE, self.base),
            (ResearchScenarioKind.BULL, self.bull),
            (ResearchScenarioKind.BEAR, self.bear),
        )

    def has_content(self) -> bool:
        return bool(self.base or self.bull or self.bear)


class ValuationAssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(min_length=1)
    low: DerivedRangeEndpointDraft
    high: DerivedRangeEndpointDraft
    limitations: tuple[str, ...] = Field(min_length=1)


class ObservedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value_ref: str = Field(pattern=r"^nv_[a-f0-9]{12}$")
    interpretation: str = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.OBSERVED] = MarketReferenceBasis.OBSERVED


class InterpretedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value: float
    interpretation: str = Field(min_length=1)
    anchor_value_refs: tuple[str, ...] = Field(min_length=1)
    context_evidence_refs: tuple[str, ...] = ()
    basis: Literal[MarketReferenceBasis.INTERPRETED] = MarketReferenceBasis.INTERPRETED

    @field_validator("anchor_value_refs")
    @classmethod
    def validate_anchor_value_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"nv_[a-f0-9]{12}", item) for item in refs):
            raise ValueError("invalid numeric anchor reference")
        return refs


class DerivedMarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    interpretation: str = Field(min_length=1)
    basis: Literal[MarketReferenceBasis.DERIVED] = MarketReferenceBasis.DERIVED
    calculation_id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")


type MarketReferenceLevelDraft = Annotated[
    ObservedMarketReferenceLevelDraft
    | InterpretedMarketReferenceLevelDraft
    | DerivedMarketReferenceLevelDraft,
    Field(discriminator="basis"),
]


class DecisionNumericDraft(BaseModel):
    """Optional scenario references, valuation, and market-reference payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    scenario_reference_ranges: ScenarioReferenceRangesDraft = Field(
        default_factory=ScenarioReferenceRangesDraft
    )
    valuation_assessment: ValuationAssessmentDraft | None = None
    market_reference_levels: tuple[MarketReferenceLevelDraft, ...] = ()
    calculation_records: tuple[CalculationRecordDraft, ...] = ()


@dataclass(frozen=True)
class ResearchDecisionOutput:
    value: ResearchDecision
    generation_method: ArtifactGenerationMethod
    numeric_generation_method: ArtifactGenerationMethod
    warnings: tuple[ResearchWarning, ...] = ()
    numeric_audit: DecisionNumericAuditAppendix | None = None
