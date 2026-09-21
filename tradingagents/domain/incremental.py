"""Incremental contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from tradingagents.domain.collection import (
    CollectionSummary,
    IncrementalEvidenceCandidate,
    InformationAdvancement,
    ResearchAvailability,
)
from tradingagents.domain.common import (
    _DECISION_COMPONENT_PATH_PATTERN,
    ArtifactGenerationMethod,
    FrozenModel,
    _StableStrEnum,
    _unique_evidence_refs,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.evidence import EvidenceBundle
from tradingagents.domain.performance import (
    BenchmarkSeriesResult,
    MarketSeriesResult,
    PerformanceObservation,
)
from tradingagents.domain.reports import ReportSection, ResearchWarning, _coerce_warnings


class ReassessmentDisposition(_StableStrEnum):
    REAFFIRMED = "reaffirmed"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    OVERTURNED = "overturned"
    UNRESOLVED = "unresolved"


class IncrementalDecisionOutcome(_StableStrEnum):
    """Whether an Incremental Node reuses or regenerates its Full Decision."""

    UNCHANGED = "unchanged"
    UPDATED = "updated"


class ResearchReassessmentEntry(FrozenModel):
    component_id: str = Field(pattern=_DECISION_COMPONENT_PATH_PATTERN.pattern)
    disposition: ReassessmentDisposition
    reason: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class ResearchReassessment(FrozenModel):
    entries: tuple[ResearchReassessmentEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_components(self) -> ResearchReassessment:
        if len({entry.component_id for entry in self.entries}) != len(self.entries):
            raise ValueError("reassessment components must be unique")
        return self


class IncrementalCollectionResult(FrozenModel):
    """Deterministic collection observations plus unsealed new Evidence."""

    collection_summary: CollectionSummary
    evidence: tuple[IncrementalEvidenceCandidate, ...] = ()
    stock_series: MarketSeriesResult | None = None
    stock_series_evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    benchmark_series: tuple[BenchmarkSeriesResult, ...] = ()

    @model_validator(mode="after")
    def validate_collection_links(self) -> IncrementalCollectionResult:
        if self.stock_series is None and self.stock_series_evidence_ref is not None:
            raise ValueError("stock-series Evidence link requires a stock series")
        names = tuple(item.name for item in self.benchmark_series)
        if len(names) != len(set(names)):
            raise ValueError("benchmark series names must be unique")
        return self


class FullResearchRequiredReason(FrozenModel):
    code: Literal[
        "thesis.material_reversal",
        "identity.uncertain",
        "attribution.unreliable",
        "evidence.material_conflict",
    ]
    message: str = Field(min_length=1)
    origin: Literal["deterministic", "semantic"]
    evidence_refs: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class IncrementalSynthesisInput(FrozenModel):
    full_baseline_run_id: str = Field(min_length=1, max_length=36)
    full_baseline_decision: ResearchDecision
    permitted_baseline_evidence_refs: tuple[str, ...] = ()
    incremental_evidence: EvidenceBundle
    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    performance: PerformanceObservation
    method_snapshot: dict[str, Any]


class IncrementalAnalysisBrief(FrozenModel):
    """User-readable Incremental analysis produced by the semantic synthesis pass."""

    markdown: str = Field(min_length=1)
    report_sections: tuple[ReportSection, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()
    prompt_version: Literal["incremental-analysis-brief-v1"] = "incremental-analysis-brief-v1"
    generation_method: Literal[ArtifactGenerationMethod.MARKDOWN_AUDITED] = (
        ArtifactGenerationMethod.MARKDOWN_AUDITED
    )

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class IncrementalSynthesis(FrozenModel):
    analysis_brief: IncrementalAnalysisBrief
    reassessment: ResearchReassessment
    decision_outcome: IncrementalDecisionOutcome
    decision_outcome_reason: str = Field(min_length=1)
    decision: ResearchDecision
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


class IncrementalNodeProducts(FrozenModel):
    analysis_brief: IncrementalAnalysisBrief | None = None
    collection_summary: CollectionSummary
    research_availability: ResearchAvailability
    information_advancement: InformationAdvancement
    performance: PerformanceObservation
    reassessment: ResearchReassessment
    decision_outcome: IncrementalDecisionOutcome | None = None
    decision_outcome_reason: str | None = Field(default=None, min_length=1)
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()

    @model_validator(mode="after")
    def validate_decision_outcome_pair(self) -> IncrementalNodeProducts:
        if (self.decision_outcome is None) != (self.decision_outcome_reason is None):
            raise ValueError(
                "decision outcome and reason must either both be recorded or both be absent"
            )
        return self


class IncrementalBaselineContext(FrozenModel):
    run_id: str = Field(min_length=1, max_length=36)
    analysis_date: date
    decision: ResearchDecision


class IncrementalRunContext(FrozenModel):
    analysis_brief: IncrementalAnalysisBrief | None = None
    full_baseline: IncrementalBaselineContext


class IncrementalExportContext(IncrementalRunContext):
    full_baseline_evidence: EvidenceBundle
