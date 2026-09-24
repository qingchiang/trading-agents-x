"""Serializer-facing research drafts and generated artifact outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    field_serializer,
)

from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
)
from tradingagents.domain.decision import (
    MarketReferenceLevel,
    ResearchDecision,
    RiskReviewAdjustment,
    ScenarioReferenceRange,
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


class ResearchScenarioDraft(ResearchScenarioCoreDraft):
    # Keep the provider schema informative while validating optional candidates
    # individually after the strict research core has been accepted.
    reference_ranges: SkipValidation[tuple[ScenarioReferenceRange, ...]] = ()

    @field_serializer("reference_ranges")
    def serialize_ranges(self, value):
        return _reference_candidates(value)


class ResearchDecisionDraft(ResearchDecisionCoreDraft):
    scenarios: tuple[ResearchScenarioDraft, ...] = Field(min_length=3, max_length=3)
    market_reference_levels: SkipValidation[tuple[MarketReferenceLevel, ...]] = ()

    @field_serializer("market_reference_levels")
    def serialize_levels(self, value):
        return _reference_candidates(value)


def _reference_candidates(value):
    if isinstance(value, (list, tuple)):
        return [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item for item in value
        ]
    return value


@dataclass(frozen=True)
class ResearchDecisionOutput:
    value: ResearchDecision
    generation_method: ArtifactGenerationMethod
    warnings: tuple[ResearchWarning, ...] = ()
