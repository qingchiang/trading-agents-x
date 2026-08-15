"""Analyst, deliberation, decision, memory, and artifact contracts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from ._contract_base import (
    _DECISION_COMPONENT_PATH_PATTERN,
    _MEMORY_REF_PATTERN,
    _RESEARCH_ID_PATTERN,
    ArtifactGenerationMethod,
    DebateImportance,
    FrozenModel,
    NumericAuditStatus,
    ResearchRating,
    ResearchScenarioKind,
    RiskReviewDisposition,
    ScenarioReferenceCategory,
    _unique_evidence_refs,
    _unique_research_ids,
)
from ._contract_evidence import MarketReferenceBasis, MeasurementKind


class AnalystClaimType(StrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class ClaimImportance(StrEnum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"


class ReportAuditStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class ReportSection(FrozenModel):
    """A deterministic heading extracted from the human-readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    title: str = Field(min_length=1)
    anchor: str = Field(min_length=1)
    source_refs: tuple[str, ...] = ()

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_evidence_refs(value)


class KeyClaim(FrozenModel):
    """A decision-relevant assertion extracted from a readable report."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    kind: AnalystClaimType
    importance: ClaimImportance
    statement: str = Field(min_length=1)
    implication: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(r"ev_[a-f0-9]{12}", ref) for ref in refs):
            raise ValueError("key claims must use valid evidence refs")
        return refs

    @field_validator("required_sources")
    @classmethod
    def validate_required_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sources = tuple(dict.fromkeys(source.strip() for source in value))
        if any(not source for source in sources):
            raise ValueError("required source names must not be empty")
        return sources


class ResearchWarning(FrozenModel):
    """Structured, plain-text warning suitable for APIs and audit exports."""

    code: str = Field(default="legacy.warning", pattern=r"^[a-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=2000)
    evidence_ref: str | None = Field(
        default=None,
        pattern=r"^ev_[a-f0-9]{12}$",
    )
    source: str | None = Field(default=None, max_length=200)

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: Any) -> str:
        text = str(value)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        text = re.sub(r"(\*\*|__|`)", "", text)
        return " ".join(text.split()).strip()


def _coerce_warnings(value: Any) -> tuple[ResearchWarning, ...]:
    if value is None:
        return ()
    items = (value,) if isinstance(value, (str, dict, ResearchWarning)) else value
    warnings = []
    for item in items:
        if isinstance(item, ResearchWarning):
            warning = item
        elif isinstance(item, str):
            warning = ResearchWarning(message=item)
        else:
            warning = ResearchWarning.model_validate(item)
        warnings.append(warning)
    return tuple(dict.fromkeys(warnings))


class AnalystReport(FrozenModel):
    """Readable analyst report with a deliberately small audit envelope."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    markdown: str = Field(min_length=1)
    report_sections: tuple[ReportSection, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    key_claims: tuple[KeyClaim, ...] = ()
    source_refs: tuple[str, ...] = ()
    audit_status: ReportAuditStatus
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_structure(self) -> AnalystReport:
        claim_ids = tuple(claim.id for claim in self.key_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("analyst claim IDs must be unique")
        section_ids = tuple(section.id for section in self.report_sections)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("analyst section IDs must be unique")
        if any(claim.section_id not in set(section_ids) for claim in self.key_claims):
            raise ValueError("key claims must identify an existing report section")
        used_refs = {ref for claim in self.key_claims for ref in claim.evidence_refs}
        used_refs.update(ref for section in self.report_sections for ref in section.source_refs)
        if not used_refs.issubset(self.source_refs):
            raise ValueError("report source refs must include claim and section refs")
        if self.audit_status is ReportAuditStatus.COMPLETE:
            if not any(claim.importance is ClaimImportance.PRIMARY for claim in self.key_claims):
                raise ValueError("complete report audit requires a primary claim")
            if any(not claim.evidence_refs for claim in self.key_claims):
                raise ValueError("complete report audit requires cited claims")
        return self


class DecisionBrief(FrozenModel):
    """Readable Final reasoning persisted before strict decision serialization."""

    markdown: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    warnings: tuple[ResearchWarning, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: Any) -> tuple[ResearchWarning, ...]:
        return _coerce_warnings(value)


class ResearchCase(FrozenModel):
    """A readable constructive or skeptical research case."""

    role: Literal["bull", "bear"]
    markdown: str = Field(min_length=1)


class DebateIssue(FrozenModel):
    """One material question used only for graph routing and navigation."""

    id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    question: str = Field(min_length=1)
    importance: DebateImportance


class DebateAgenda(FrozenModel):
    """Prioritized shallow agenda derived from the two readable cases."""

    summary: str = Field(min_length=1)
    issues: tuple[DebateIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_agenda(self) -> DebateAgenda:
        issue_ids = tuple(issue.id for issue in self.issues)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("debate issue IDs must be unique")
        return self


class RebuttalReview(FrozenModel):
    """One readable response plus the issue IDs needed by graph control."""

    role: Literal["bull", "bear"]
    round: int = Field(ge=1)
    markdown: str = Field(min_length=1)
    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()

    @field_validator("addressed_issue_ids", "open_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)


class IssueDisposition(FrozenModel):
    """A judge routing result without duplicating the readable rationale."""

    issue_id: str = Field(pattern=r"^debate\.issue_[a-z0-9][a-z0-9_.-]*$")
    status: Literal["upheld", "rejected", "unresolved"]


class JudgeDraft(FrozenModel):
    """Readable preliminary judgment with shallow issue dispositions."""

    markdown: str = Field(min_length=1)
    preliminary_rating: ResearchRating | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_draft(self) -> JudgeDraft:
        issue_ids = tuple(item.issue_id for item in self.issue_dispositions)
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("judge dispositions must use unique issue IDs")
        return self


class RiskReview(FrozenModel):
    """A readable challenge with only navigation metadata typed."""

    role: Literal["integrated", "aggressive", "neutral", "conservative"]
    markdown: str = Field(min_length=1)
    challenged_issue_ids: tuple[str, ...] = ()
    unresolved_issue_ids: tuple[str, ...] = ()

    @field_validator("challenged_issue_ids", "unresolved_issue_ids")
    @classmethod
    def validate_issue_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_research_ids(value)


class NumericTemporalBasis(StrEnum):
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


class ResearchQuestionSourceDependency(FrozenModel):
    question: str = Field(min_length=1)
    required_sources: tuple[str, ...] = Field(min_length=1, max_length=8)

    @field_validator("required_sources")
    @classmethod
    def validate_required_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        sources = tuple(dict.fromkeys(source.strip() for source in value))
        if any(not source for source in sources):
            raise ValueError("required source names must not be empty")
        return sources


class ResearchDecision(FrozenModel):
    """Research-only conclusion; deliberately excludes account-level advice."""

    rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    memory_refs: tuple[str, ...] = ()
    catalysts: tuple[str, ...] = ()
    risks: tuple[str, ...] = Field(min_length=1)
    invalidation_conditions: tuple[str, ...] = Field(min_length=1)
    unresolved_questions: tuple[str, ...] = ()
    question_source_dependencies: tuple[ResearchQuestionSourceDependency, ...] = ()
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

    @field_validator("memory_refs")
    @classmethod
    def validate_memory_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        if any(not _MEMORY_REF_PATTERN.fullmatch(ref) for ref in refs):
            raise ValueError("memory refs must use the memory:<run_id> format")
        return refs

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _unique_evidence_refs(value)

    @model_validator(mode="after")
    def validate_scenario_set(self) -> ResearchDecision:
        dependency_questions = tuple(item.question for item in self.question_source_dependencies)
        if len(dependency_questions) != len(set(dependency_questions)):
            raise ValueError("question source dependencies must be unique")
        if not set(dependency_questions).issubset(self.unresolved_questions):
            raise ValueError("source dependencies must name unresolved questions")
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


def _field_value(value: Any, field: str) -> Any:
    if isinstance(value, BaseModel):
        return getattr(value, field, None)
    if isinstance(value, dict):
        return value.get(field)
    return None


class MemoryOutcome(FrozenModel):
    """Completed five-or-more-interval feedback for one past decision."""

    benchmark: str
    observation_start: date | None = None
    observation_end: date | None = None
    holding_intervals: int = Field(ge=5)
    raw_return: float
    alpha_return: float


class MemoryRecord(FrozenModel):
    """One auditable memory item supplied to a research decision node."""

    ref: str
    run_id: str
    scope: Literal["same_ticker", "same_market"]
    ticker: str
    market: str | None = None
    analysis_date: date
    decision: ResearchDecision | None = None
    outcome: MemoryOutcome | None = None
    reflection: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_record(self) -> MemoryRecord:
        if not _MEMORY_REF_PATTERN.fullmatch(self.ref):
            raise ValueError("memory ref must use the memory:<run_id> format")
        if self.ref != f"memory:{self.run_id}":
            raise ValueError("memory ref must identify its run_id")
        if self.scope == "same_ticker":
            if self.decision is None or self.outcome is None:
                raise ValueError("same-ticker memory requires decision and outcome")
        elif self.decision is not None or self.outcome is not None:
            raise ValueError("same-market memory must contain reflection-only feedback")
        return self

    def prompt_text(self, max_chars: int = 2000) -> str:
        """Render one bounded block without turning memory into evidence."""
        parts = [
            f"REF: {self.ref}",
            f"SCOPE: {self.scope}",
            (f"PAST RUN: {self.analysis_date} | {self.ticker} | {self.market or 'unknown market'}"),
        ]
        if self.decision is not None:
            parts.append(
                "PAST DECISION:\n"
                + json.dumps(
                    self.decision.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        if self.outcome is not None:
            parts.append(
                "OBSERVED OUTCOME:\n"
                + json.dumps(
                    self.outcome.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        parts.append(f"REFLECTION:\n{self.reflection}")
        rendered = "\n".join(parts)
        if max_chars <= 0:
            return ""
        if len(rendered) <= max_chars:
            return rendered
        if max_chars == 1:
            return "…"
        return rendered[: max_chars - 1] + "…"


class MemoryContext(FrozenModel):
    """Deterministic, bounded historical feedback for one current run."""

    version: Literal["1"] = "1"
    instrument: str
    market: str | None = None
    items: tuple[MemoryRecord, ...] = ()

    @model_validator(mode="after")
    def validate_context(self) -> MemoryContext:
        refs = [item.ref for item in self.items]
        if len(refs) != len(set(refs)):
            raise ValueError("memory refs must be unique")
        instrument = self.instrument.casefold()
        for item in self.items:
            if item.scope == "same_ticker" and item.ticker.casefold() != instrument:
                raise ValueError("same-ticker memory must match the current instrument")
            if item.scope == "same_market" and (
                item.ticker.casefold() == instrument
                or self.market is None
                or item.market != self.market
            ):
                raise ValueError(
                    "same-market memory must be another instrument in the current market"
                )
        return self

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    def prompt_text(
        self,
        *,
        max_chars: int = 12_000,
        item_max_chars: int = 2_000,
    ) -> str:
        if not self.items or max_chars <= 0 or item_max_chars <= 0:
            return ""
        separators = 2 * (len(self.items) - 1)
        available = max(0, max_chars - separators)
        per_item = min(item_max_chars, available // len(self.items))
        if per_item <= 0:
            return ""
        return "\n\n".join(item.prompt_text(per_item) for item in self.items)[:max_chars]


ResearchArtifactContent = (
    AnalystReport
    | DecisionBrief
    | ResearchCase
    | DebateAgenda
    | RebuttalReview
    | JudgeDraft
    | RiskReview
    | ResearchDecision
)


class ArtifactGenerationObservation(FrozenModel):
    """One structured-generation path used to produce an artifact component."""

    node: str = Field(min_length=1, max_length=160)
    task_kind: Literal["semantic_structured", "schema_serialization"]
    client_role: Literal[
        "quick_reasoning",
        "deep_reasoning",
        "quick_serializer",
        "deep_serializer",
    ]
    generation_method: ArtifactGenerationMethod


def _artifact_content_type(content: ResearchArtifactContent) -> str:
    if isinstance(content, AnalystReport):
        return "analyst_report"
    if isinstance(content, DecisionBrief):
        return "decision_brief"
    if isinstance(content, ResearchCase):
        return "research_case"
    if isinstance(content, DebateAgenda):
        return "debate_agenda"
    if isinstance(content, RebuttalReview):
        return "rebuttal_review"
    if isinstance(content, JudgeDraft):
        return "judge_draft"
    if isinstance(content, RiskReview):
        return "risk_review"
    if isinstance(content, ResearchDecision):
        return "research_decision"
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


class ResearchArtifactDraft(FrozenModel):
    """Typed graph output awaiting application-owned persistence metadata."""

    node: str = Field(min_length=1, max_length=160)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["2"] = "2"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
    content: ResearchArtifactContent

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(
            self.content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class ResearchArtifact(FrozenModel):
    """Durable, typed output from one visible research stage."""

    id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    attempt: int = Field(ge=1)
    stage: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    role: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    round: int = Field(default=0, ge=0)
    schema_version: Literal["2"] = "2"
    prompt_version: str = Field(
        default="research-v1",
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    )
    generation_method: ArtifactGenerationMethod
    generation_observations: tuple[ArtifactGenerationObservation, ...] = ()
    content: ResearchArtifactContent
    created_at: datetime

    @property
    def content_type(self) -> str:
        return _artifact_content_type(self.content)
