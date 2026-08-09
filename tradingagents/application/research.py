"""Versioned contracts for longitudinal Research Chains and Revisions."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagents.dataflows.symbol_utils import market_timezone

from .contracts import (
    AnalysisRequest,
    AnalystReport,
    EvidenceBundle,
    MarketReferenceLevel,
    ReportLanguage,
    ResearchDecision,
    ResearchRating,
    ResearchScenarioKind,
    ResearchUpdateAudit,
    RunMetrics,
    report_language_value,
)

_CLAIM_ID = r"^claim_[a-f0-9]{32}$"
_QUESTION_ID = r"^question_[a-f0-9]{32}$"
_EVIDENCE_REF = r"^ev_[a-f0-9]{12}$"


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FullResearchExecution(Protocol):
    evidence: EvidenceBundle
    decision: ResearchDecision
    reports: dict[str, AnalystReport]


class DecisionConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class ClaimConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class ScenarioLikelihood(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class EpistemicKind(str, Enum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class DecisionRole(str, Enum):
    THESIS = "thesis"
    RISK = "risk"
    CATALYST = "catalyst"
    INVALIDATION = "invalidation"
    SCENARIO_ASSUMPTION = "scenario_assumption"


class ClaimStanding(str, Enum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    RETIRED = "retired"


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"


class CoverageRequirement(str, Enum):
    REQUIRED = "required"
    ADVISORY = "advisory"


class SourceRecordStatus(str, Enum):
    PUBLISHED = "published"
    CORRECTED = "corrected"
    WITHDRAWN = "withdrawn"
    REPLACED = "replaced"


class SourceRecordKind(str, Enum):
    DISCLOSURE = "disclosure"
    FUNDAMENTAL = "fundamental"
    MARKET = "market"


class ResearchChangeKind(str, Enum):
    NEW_FUNDAMENTAL_FILING = "new_fundamental_filing"
    FUNDAMENTAL_CORRECTION = "fundamental_correction"
    FUNDAMENTAL_RESTATEMENT = "fundamental_restatement"
    ACCOUNTING_SCOPE_CHANGE = "accounting_scope_change"
    UNCLASSIFIABLE_FUNDAMENTAL_CHANGE = "unclassifiable_fundamental_change"
    MARKET_SEMANTIC_INCOMPATIBILITY = "market_semantic_incompatibility"
    MARKET_BOUNDARY_CROSSING = "market_boundary_crossing"
    ORDINARY_MARKET_MOVE = "ordinary_market_move"
    UNCHANGED_OBSERVATION = "unchanged_observation"


class ResearchExecutionStrategy(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ResearchRevisionRole(str, Enum):
    INITIAL = "initial"
    UPDATE = "update"


class ResearchChangeConclusion(str, Enum):
    MATERIAL_CHANGE = "material_change"
    NO_MATERIAL_CHANGE = "no_material_change"
    INDETERMINATE = "indeterminate"


class IndeterminateReason(str, Enum):
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    QUESTION_DISPOSITION_LIMITED = "question_disposition_limited"


class NextUpdateReason(str, Enum):
    INDETERMINATE_HEAD = "indeterminate_head"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    INCOMPATIBLE_MARKET_SEMANTICS = "incompatible_market_semantics"


class IncrementalEscalationReason(str, Enum):
    INVALID_BASELINE = "invalid_baseline"
    SOURCE_CORRECTION = "source_correction"
    SOURCE_WITHDRAWAL = "source_withdrawal"
    SOURCE_REPLACEMENT = "source_replacement"
    SOURCE_VERSION_CHANGE = "source_version_change"
    INCOMPATIBLE_SEMANTICS = "incompatible_semantics"
    THRESHOLD_CROSSING = "threshold_crossing"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    SCHEMA_INVALID = "schema_invalid"
    SEMANTIC_WEAKENING = "semantic_weakening"
    SEMANTIC_CONTRADICTION = "semantic_contradiction"
    SEMANTIC_ANSWERING = "semantic_answering"
    SEMANTIC_REOPENING = "semantic_reopening"
    SEMANTIC_UNCERTAINTY = "semantic_uncertainty"
    POTENTIALLY_MATERIAL_NOVELTY = "potentially_material_novelty"
    CONFIDENCE_CHANGE = "confidence_change"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    SEMANTIC_OUTPUT_INVALID = "semantic_output_invalid"
    SEMANTIC_INPUT_OVERSIZE = "semantic_input_oversize"


class SemanticChangeRelationship(str, Enum):
    SUPPORT = "support"
    WEAKENING = "weakening"
    CONTRADICTION = "contradiction"
    ANSWERING = "answering"
    REOPENING = "reopening"
    IRRELEVANCE = "irrelevance"
    UNCERTAINTY = "uncertainty"
    POTENTIALLY_MATERIAL_NOVELTY = "potentially_material_novelty"


class ClaimChange(str, Enum):
    INTRODUCED = "introduced"
    REAFFIRMED = "reaffirmed"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    INVALIDATED = "invalidated"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


class QuestionChange(str, Enum):
    INTRODUCED = "introduced"
    REAFFIRMED = "reaffirmed"
    ANSWERED = "answered"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class QuestionDispositionKind(str, Enum):
    REAFFIRMED = "reaffirmed"
    ANSWERED = "answered"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class QuestionDispositionLimitation(str, Enum):
    OUTPUT_INVALID = "question_disposition_output_invalid"
    EVIDENCE_INVALID = "question_disposition_evidence_invalid"
    AMBIGUOUS_IDENTITY = "question_disposition_ambiguous_identity"
    INCOMPLETE = "question_disposition_incomplete"


def _status_after_question_disposition(
    disposition: QuestionDispositionKind,
    previous_status: QuestionStatus,
) -> QuestionStatus:
    return {
        QuestionDispositionKind.ANSWERED: QuestionStatus.ANSWERED,
        QuestionDispositionKind.REOPENED: QuestionStatus.OPEN,
        QuestionDispositionKind.SUPERSEDED: QuestionStatus.SUPERSEDED,
        QuestionDispositionKind.RETIRED: QuestionStatus.RETIRED,
    }.get(disposition, previous_status)


class IdentityDisposition(str, Enum):
    EXACT_MATCH = "exact_match"
    NEW = "new"
    AMBIGUOUS_NEW = "ambiguous_new"
    CONSERVATIVE_RETIREMENT = "conservative_retirement"


class ResearchClaim(ResearchModel):
    id: str = Field(pattern=_CLAIM_ID)
    statement: str = Field(min_length=1)
    epistemic_kind: EpistemicKind
    decision_role: DecisionRole
    standing: ClaimStanding = ClaimStanding.ACTIVE
    confidence: ClaimConfidence
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    observed_at: date | None = None
    falsifier: str | None = Field(default=None, min_length=1)
    evidence_relationship: Literal["direct", "decision_envelope"] = "direct"
    required_sources: tuple[str, ...] = ()

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        refs = tuple(dict.fromkeys(value))
        import re

        if any(not re.fullmatch(_EVIDENCE_REF, ref) for ref in refs):
            raise ValueError("claims must use valid Evidence refs")
        return refs

    @model_validator(mode="after")
    def validate_epistemic_contract(self) -> ResearchClaim:
        if self.epistemic_kind is EpistemicKind.OBSERVATION:
            if self.observed_at is None:
                raise ValueError("observation claims require observed_at")
        elif not self.falsifier:
            raise ValueError("inference and forecast claims require a falsifier")
        return self


class ResearchQuestion(ResearchModel):
    id: str = Field(pattern=_QUESTION_ID)
    question: str = Field(min_length=1)
    status: QuestionStatus = QuestionStatus.OPEN
    evidence_refs: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    successor_question_id: str | None = Field(default=None, pattern=_QUESTION_ID)
    last_disposition: QuestionDispositionKind | None = None
    disposition_reason: str | None = Field(default=None, min_length=1, max_length=1000)


class ResearchOpinion(ResearchModel):
    rating: ResearchRating
    confidence: DecisionConfidence
    thesis: str = Field(min_length=1)
    primary_claim_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ResearchScenarioState(ResearchModel):
    kind: ResearchScenarioKind
    likelihood: ScenarioLikelihood
    cutoff: date
    horizon: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    assumption_claim_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class ResearchFactor(ResearchModel):
    statement: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class CurrentResearchState(ResearchModel):
    schema_version: Literal["1"] = "1"
    prompt_version: str = "research-state-assembly-v1"
    language: str
    instrument: str = Field(min_length=1)
    cutoff: date
    opinion: ResearchOpinion
    claims: tuple[ResearchClaim, ...] = Field(min_length=1)
    questions: tuple[ResearchQuestion, ...] = ()
    scenarios: tuple[ResearchScenarioState, ...] = Field(min_length=3, max_length=3)
    risks: tuple[ResearchFactor, ...] = ()
    catalysts: tuple[ResearchFactor, ...] = ()
    invalidation_conditions: tuple[ResearchFactor, ...] = ()
    market_reference_levels: tuple[MarketReferenceLevel, ...] = ()
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: ReportLanguage | str) -> str:
        return report_language_value(value)

    @model_validator(mode="after")
    def validate_state_relationships(self) -> CurrentResearchState:
        claim_ids = tuple(claim.id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Research Claim IDs must be unique")
        question_ids = tuple(question.id for question in self.questions)
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Research Question IDs must be unique")
        if any(
            question.successor_question_id is not None
            and (
                question.successor_question_id == question.id
                or question.successor_question_id not in set(question_ids)
            )
            for question in self.questions
        ):
            raise ValueError("Question successors must identify another Question in the state")
        active_ids = {claim.id for claim in self.claims if claim.standing is ClaimStanding.ACTIVE}
        if not set(self.opinion.primary_claim_ids).issubset(active_ids):
            raise ValueError("opinion primary claims must be active")
        kinds = tuple(scenario.kind for scenario in self.scenarios)
        if len(set(kinds)) != 3 or set(kinds) != set(ResearchScenarioKind):
            raise ValueError("state requires unique base, bull, and bear scenarios")
        if any(scenario.cutoff != self.cutoff for scenario in self.scenarios):
            raise ValueError("scenarios must use the state cutoff")
        if len({scenario.horizon for scenario in self.scenarios}) != 1:
            raise ValueError("scenarios must share horizon")
        linked_ids = {
            claim_id for scenario in self.scenarios for claim_id in scenario.assumption_claim_ids
        }
        for factor in (*self.risks, *self.catalysts, *self.invalidation_conditions):
            linked_ids.update(factor.claim_ids)
        if not linked_ids.issubset(active_ids):
            raise ValueError("state relationships must use active Claim IDs")
        linked_refs = set(self.opinion.evidence_refs)
        linked_refs.update(ref for claim in self.claims for ref in claim.evidence_refs)
        linked_refs.update(ref for question in self.questions for ref in question.evidence_refs)
        linked_refs.update(ref for scenario in self.scenarios for ref in scenario.evidence_refs)
        linked_refs.update(
            ref
            for factor in (*self.risks, *self.catalysts, *self.invalidation_conditions)
            for ref in factor.evidence_refs
        )
        linked_refs.update(
            ref for level in self.market_reference_levels for ref in level.evidence_refs
        )
        if not linked_refs.issubset(self.evidence_refs):
            raise ValueError("state relationships reference unknown Evidence")
        if any(level.as_of_date > self.cutoff for level in self.market_reference_levels):
            raise ValueError("market reference levels must not be after the state cutoff")
        return self


class ResearchDomainCoverage(ResearchModel):
    domain: str = Field(min_length=1)
    status: CoverageStatus
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    requirement: CoverageRequirement = CoverageRequirement.REQUIRED
    source: str | None = None


class ResearchObjectCoverage(ResearchModel):
    object_id: str
    status: CoverageStatus
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class CoverageAttestation(ResearchModel):
    schema_version: Literal["1"] = "1"
    claims: tuple[ResearchObjectCoverage, ...]
    questions: tuple[ResearchObjectCoverage, ...]
    domains: tuple[ResearchDomainCoverage, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    supports_no_material_change: bool = True


class UpdateSummary(ResearchModel):
    schema_version: Literal["1"] = "1"
    language: str
    summary: str = Field(min_length=1)
    checked_domains: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    baseline_cutoff: date | None = None
    analysis_cutoff: date | None = None
    execution_strategy: ResearchExecutionStrategy | None = None
    change_conclusion: ResearchChangeConclusion | None = None
    new_evidence_refs: tuple[str, ...] = ()

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: ReportLanguage | str) -> str:
        return report_language_value(value)


class EvidenceSnapshotItem(ResearchModel):
    evidence_ref: str = Field(pattern=_EVIDENCE_REF)
    lineage: Literal["new", "inherited"]
    source_revision_id: str | None = None


class SourceRecordVersion(ResearchModel):
    source: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    status: SourceRecordStatus
    published_at: str = Field(min_length=1)
    available_at: datetime
    title: str = Field(min_length=1)
    availability_basis: str | None = None
    url: str | None = None
    replaces_version_id: str | None = None
    evidence_ref: str = Field(pattern=_EVIDENCE_REF)
    fallback: bool = False
    record_kind: SourceRecordKind = SourceRecordKind.DISCLOSURE
    native_record_id: str | None = None
    comparison_key: str | None = None
    change_hint: (
        Literal[
            "new_filing",
            "correction",
            "restatement",
            "accounting_scope_change",
            "unclassifiable",
        ]
        | None
    ) = None
    accounting_scope: str | None = None
    adjustment: str | None = None
    observation_value: float | None = None
    unit: str | None = None
    precision: int | None = Field(default=None, ge=0)

    @field_validator("available_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Source Record Version available_at requires timezone")
        return value


class SourceRecordSnapshotItem(ResearchModel):
    version_id: str = Field(min_length=1)
    lineage: Literal["new", "inherited"]
    observed_in_execution: bool
    source_revision_id: str | None = None


class SourceWatermarkSnapshot(ResearchModel):
    source: str = Field(min_length=1)
    scanned_start: date
    scanned_end: date
    status: CoverageStatus
    temporal_scope: Literal["point_in_time", "live_only", "unknown"] = "point_in_time"
    limitations: tuple[str, ...] = ()
    returned_records: int = Field(default=0, ge=0)
    reported_records: int | None = Field(default=None, ge=0)
    baseline_cutoff: date | None = None
    overlap_start: date | None = None

    @model_validator(mode="after")
    def validate_window(self) -> SourceWatermarkSnapshot:
        if self.scanned_start > self.scanned_end:
            raise ValueError("Source Watermark start must not follow end")
        if self.overlap_start is not None and not (
            self.scanned_start <= self.overlap_start <= self.scanned_end
        ):
            raise ValueError("Source Watermark overlap must be inside scanned interval")
        return self


class EffectiveEvidenceSnapshot(ResearchModel):
    schema_version: Literal["1"] = "1"
    bundle: EvidenceBundle
    lineage: tuple[EvidenceSnapshotItem, ...]
    source_records: tuple[SourceRecordVersion, ...] = ()
    source_record_lineage: tuple[SourceRecordSnapshotItem, ...] = ()
    source_watermarks: tuple[SourceWatermarkSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_lineage(self) -> EffectiveEvidenceSnapshot:
        refs = {item.ref for item in self.bundle.items}
        if {item.evidence_ref for item in self.lineage} != refs:
            raise ValueError("Evidence lineage must cover the complete bundle")
        if any(
            item.lineage == "inherited" and not item.source_revision_id for item in self.lineage
        ):
            raise ValueError("inherited Evidence requires a source Revision")
        version_ids = tuple(item.version_id for item in self.source_records)
        if len(version_ids) != len(set(version_ids)):
            raise ValueError("Source Record Version identities must be unique")
        lineage_ids = tuple(item.version_id for item in self.source_record_lineage)
        if set(version_ids) != set(lineage_ids) or len(lineage_ids) != len(set(lineage_ids)):
            raise ValueError("Source Record lineage must cover every version exactly once")
        if any(
            item.lineage == "inherited" and not item.source_revision_id
            for item in self.source_record_lineage
        ):
            raise ValueError("inherited Source Record Versions require a source Revision")
        return self


class ClaimRevisionDelta(ResearchModel):
    object_id: str = Field(pattern=_CLAIM_ID)
    previous_object_id: str | None = Field(default=None, pattern=_CLAIM_ID)
    change: ClaimChange
    identity_disposition: IdentityDisposition


class QuestionRevisionDelta(ResearchModel):
    object_id: str = Field(pattern=_QUESTION_ID)
    previous_object_id: str | None = Field(default=None, pattern=_QUESTION_ID)
    change: QuestionChange
    identity_disposition: IdentityDisposition
    evidence_refs: tuple[str, ...] = ()
    reason: str | None = Field(default=None, min_length=1)
    successor_object_id: str | None = Field(default=None, pattern=_QUESTION_ID)


class QuestionDispositionRecord(ResearchModel):
    baseline_question_id: str = Field(pattern=_QUESTION_ID)
    disposition: QuestionDispositionKind
    candidate_question_id: str | None = Field(default=None, pattern=_QUESTION_ID)
    successor_question_id: str | None = Field(default=None, pattern=_QUESTION_ID)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_successor(self) -> QuestionDispositionRecord:
        if self.disposition is QuestionDispositionKind.SUPERSEDED:
            if self.successor_question_id is None or self.candidate_question_id is not None:
                raise ValueError("supersession requires only a successor Question")
        elif self.successor_question_id is not None:
            raise ValueError("only supersession may identify a successor Question")
        return self


class QuestionDispositionAudit(ResearchModel):
    schema_version: Literal["1"] = "1"
    status: Literal["complete", "limited"]
    language: str
    dispositions: tuple[QuestionDispositionRecord, ...] = Field(default=(), max_length=64)
    limitation_reason: QuestionDispositionLimitation | None = None
    repair_attempted: bool = False

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: ReportLanguage | str) -> str:
        return report_language_value(value)

    @model_validator(mode="after")
    def validate_status(self) -> QuestionDispositionAudit:
        if (self.status == "limited") != (self.limitation_reason is not None):
            raise ValueError("limited Question Disposition requires one stable reason")
        if self.status == "limited" and self.dispositions:
            raise ValueError("limited Question Disposition cannot apply dispositions")
        if self.status == "complete" and not self.dispositions:
            raise ValueError("complete Question Disposition must cover baseline Questions")
        return self


class RevisionDelta(ResearchModel):
    schema_version: Literal["1"] = "1"
    opinion_changed: bool
    claims: tuple[ClaimRevisionDelta, ...]
    questions: tuple[QuestionRevisionDelta, ...]
    changed_sections: tuple[
        Literal[
            "opinion",
            "claims",
            "questions",
            "scenarios",
            "risks",
            "catalysts",
            "invalidation_conditions",
        ],
        ...,
    ] = ()
    inherited_evidence_refs: tuple[str, ...] = ()
    new_evidence_refs: tuple[str, ...] = ()
    change_signals: tuple[ResearchChangeSignal, ...] = ()
    question_disposition: QuestionDispositionAudit | None = None


class ResearchChangeSignal(ResearchModel):
    kind: ResearchChangeKind
    domain: Literal["fundamentals", "market"]
    record_id: str = Field(min_length=1)
    previous_version_id: str | None = None
    current_version_id: str | None = None
    requires_full_analysis: bool
    detail: str = Field(min_length=1)
    boundary_label: str | None = None
    boundary_value: float | None = None
    previous_value: float | None = None
    current_value: float | None = None


class ResearchRevisionDraft(ResearchModel):
    cutoff: date
    role: ResearchRevisionRole
    execution_strategy: ResearchExecutionStrategy
    change_conclusion: ResearchChangeConclusion | None = None
    indeterminate_reason: IndeterminateReason | None = None
    delta: RevisionDelta
    current_state: CurrentResearchState
    coverage: CoverageAttestation
    update_summary: UpdateSummary
    evidence_snapshot: EffectiveEvidenceSnapshot
    research_update_audit: ResearchUpdateAudit | None = None

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> ResearchRevisionDraft:
        if self.role is ResearchRevisionRole.INITIAL:
            if self.change_conclusion is not None:
                raise ValueError("initial Revision has no Change Conclusion")
            if self.execution_strategy is not ResearchExecutionStrategy.FULL:
                raise ValueError("initial Revision requires Full execution")
        elif self.change_conclusion is None:
            raise ValueError("update Revision requires a Change Conclusion")
        if self.change_conclusion is ResearchChangeConclusion.INDETERMINATE:
            if self.execution_strategy is not ResearchExecutionStrategy.FULL:
                raise ValueError("Indeterminate Revision requires Full execution")
            if self.indeterminate_reason is None:
                raise ValueError("Indeterminate Revision requires a stable reason")
        elif self.indeterminate_reason is not None:
            raise ValueError("only an Indeterminate Revision has an indeterminate reason")
        claim_ids = {claim.id for claim in self.current_state.claims}
        covered_claim_ids = tuple(item.object_id for item in self.coverage.claims)
        if claim_ids != set(covered_claim_ids) or len(covered_claim_ids) != len(
            set(covered_claim_ids)
        ):
            raise ValueError("Coverage must attest every Research Claim exactly once")
        question_ids = {question.id for question in self.current_state.questions}
        covered_question_ids = tuple(item.object_id for item in self.coverage.questions)
        if question_ids != set(covered_question_ids) or len(covered_question_ids) != len(
            set(covered_question_ids)
        ):
            raise ValueError("Coverage must attest every Research Question exactly once")
        question_disposition = self.delta.question_disposition
        if question_disposition is not None and question_disposition.status == "complete":
            questions_by_id = {item.id: item for item in self.current_state.questions}
            question_delta_by_id = {item.object_id: item for item in self.delta.questions}
            for item in question_disposition.dispositions:
                question = questions_by_id.get(item.baseline_question_id)
                delta = question_delta_by_id.get(item.baseline_question_id)
                if question is None or delta is None:
                    raise ValueError("applied Question Disposition must remain in state and delta")
                if delta.change.value != item.disposition.value:
                    raise ValueError("Question Disposition must agree with the Revision delta")
                if delta.evidence_refs != item.evidence_refs or delta.reason != item.reason:
                    raise ValueError("Question Disposition support must agree with the delta")
                if question.status is not _status_after_question_disposition(
                    item.disposition, question.status
                ):
                    raise ValueError("Question Disposition must agree with Question status")
                if (
                    question.last_disposition is not item.disposition
                    or question.disposition_reason != item.reason
                ):
                    raise ValueError("Question Disposition must remain durable in state")
                if question.successor_question_id != item.successor_question_id:
                    raise ValueError("Question successor must agree with its disposition")
        snapshot_refs = {item.ref for item in self.evidence_snapshot.bundle.items}
        reachable_refs = set(self.current_state.evidence_refs)
        reachable_refs.update(ref for item in self.coverage.claims for ref in item.evidence_refs)
        reachable_refs.update(ref for item in self.coverage.questions for ref in item.evidence_refs)
        reachable_refs.update(ref for item in self.coverage.domains for ref in item.evidence_refs)
        reachable_refs.update(self.delta.inherited_evidence_refs)
        reachable_refs.update(self.delta.new_evidence_refs)
        if self.delta.question_disposition is not None:
            reachable_refs.update(
                ref
                for item in self.delta.question_disposition.dispositions
                for ref in item.evidence_refs
            )
        reachable_refs.update(self.update_summary.new_evidence_refs)
        reachable_refs.update(item.evidence_ref for item in self.evidence_snapshot.source_records)
        if self.research_update_audit is not None:
            audit_coverages = tuple(
                item
                for coverage in (
                    self.research_update_audit.coverage,
                    (
                        self.research_update_audit.candidate.coverage
                        if self.research_update_audit.candidate is not None
                        else None
                    ),
                )
                if coverage is not None
                for item in (*coverage.claims, *coverage.questions, *coverage.domains)
            )
            reachable_refs.update(ref for item in audit_coverages for ref in item.evidence_refs)
            if self.research_update_audit.candidate is not None:
                reachable_refs.update(
                    self.research_update_audit.candidate.update_summary.new_evidence_refs
                )
                reachable_refs.update(
                    item.ref
                    for item in self.research_update_audit.candidate.evidence_snapshot.bundle.items
                )
            reachable_refs.update(
                item.evidence_ref for item in self.research_update_audit.evidence_lineage
            )
            if self.research_update_audit.semantic_assessment is not None:
                reachable_refs.update(
                    ref
                    for relationship in self.research_update_audit.semantic_assessment.relationships
                    for ref in relationship.evidence_refs
                )
        missing_refs = sorted(reachable_refs - snapshot_refs)
        if missing_refs:
            raise ValueError(
                "Evidence closure requires every reachable reference in the snapshot: "
                + ", ".join(missing_refs)
            )
        version_ids = {item.version_id for item in self.evidence_snapshot.source_records}
        referenced_versions = {
            item.replaces_version_id
            for item in self.evidence_snapshot.source_records
            if item.replaces_version_id is not None
        }
        referenced_versions.update(
            version_id
            for signal in self.delta.change_signals
            for version_id in (signal.previous_version_id, signal.current_version_id)
            if version_id is not None
        )
        if self.research_update_audit is not None and self.research_update_audit.candidate:
            referenced_versions.update(
                item.version_id
                for item in self.research_update_audit.candidate.evidence_snapshot.source_records
            )
        missing_versions = sorted(referenced_versions - version_ids)
        if missing_versions:
            raise ValueError(
                "Source Record closure requires every lineage version in the snapshot: "
                + ", ".join(missing_versions)
            )
        return self


class SemanticEvidenceRelationship(ResearchModel):
    """A model-suggested relationship resolved by application-owned identities."""

    evidence_refs: tuple[str, ...] = Field(min_length=1)
    relationship: SemanticChangeRelationship
    suggested_claim_ids: tuple[str, ...] = ()
    suggested_question_ids: tuple[str, ...] = ()
    suggested_claim_confidence: ClaimConfidence | None = None

    @field_validator("evidence_refs")
    @classmethod
    def validate_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        import re

        refs = tuple(dict.fromkeys(value))
        if any(not re.fullmatch(_EVIDENCE_REF, ref) for ref in refs):
            raise ValueError("semantic relationships must use valid Evidence refs")
        return refs


class SemanticChangeAssessment(ResearchModel):
    schema_version: Literal["1"] = "1"
    language: str
    summary: str = Field(min_length=1, max_length=2000)
    relationships: tuple[SemanticEvidenceRelationship, ...] = Field(min_length=1)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: ReportLanguage | str) -> str:
        return report_language_value(value)


class IncrementalGateResult(ResearchModel):
    """Result of bounded deterministic and optional semantic assessment."""

    candidate: ResearchRevisionDraft | None = None
    escalation_reason: IncrementalEscalationReason | None = None
    coverage: CoverageAttestation | None = None
    evidence_snapshot: EffectiveEvidenceSnapshot | None = None
    semantic_assessment: SemanticChangeAssessment | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)

    @model_validator(mode="after")
    def require_candidate_or_escalation(self) -> IncrementalGateResult:
        if (self.candidate is None) == (self.escalation_reason is None):
            raise ValueError("incremental gate requires exactly one candidate or escalation")
        if self.candidate is not None and (
            self.candidate.execution_strategy is not ResearchExecutionStrategy.INCREMENTAL
            or self.candidate.change_conclusion is not ResearchChangeConclusion.NO_MATERIAL_CHANGE
        ):
            raise ValueError("incremental gate candidate must propose No Material Change")
        return self


def _semantic_state_payload(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _semantic_state_payload(item)
            for key, item in value.items()
            if key not in {"cutoff", "evidence_refs"}
        }
    if isinstance(value, (list, tuple)):
        return tuple(_semantic_state_payload(item) for item in value)
    return value


def validate_experimental_nmc_candidate(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
) -> IncrementalEscalationReason | None:
    """Fail closed unless a bounded candidate can safely become authoritative."""
    if derive_next_update_policy(baseline)[0] != "incremental_allowed":
        return IncrementalEscalationReason.INVALID_BASELINE
    if (
        candidate.execution_strategy is not ResearchExecutionStrategy.INCREMENTAL
        or candidate.change_conclusion is not ResearchChangeConclusion.NO_MATERIAL_CHANGE
        or candidate.cutoff <= baseline.cutoff
        or candidate.current_state.instrument != baseline.current_state.instrument
        or candidate.current_state.cutoff != candidate.cutoff
        or candidate.evidence_snapshot.bundle.analysis_date != candidate.cutoff
    ):
        return IncrementalEscalationReason.SCHEMA_INVALID
    status_reasons = {
        SourceRecordStatus.CORRECTED: IncrementalEscalationReason.SOURCE_CORRECTION,
        SourceRecordStatus.WITHDRAWN: IncrementalEscalationReason.SOURCE_WITHDRAWAL,
        SourceRecordStatus.REPLACED: IncrementalEscalationReason.SOURCE_REPLACEMENT,
    }
    source_lineage = {
        item.version_id: item for item in candidate.evidence_snapshot.source_record_lineage
    }
    for record in candidate.evidence_snapshot.source_records:
        if record.status in status_reasons and source_lineage[record.version_id].lineage == "new":
            return status_reasons[record.status]
    for signal in candidate.delta.change_signals:
        if not signal.requires_full_analysis:
            continue
        if signal.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING:
            return IncrementalEscalationReason.THRESHOLD_CROSSING
        if signal.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY:
            return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
        if signal.kind is ResearchChangeKind.FUNDAMENTAL_CORRECTION:
            return IncrementalEscalationReason.SOURCE_CORRECTION
        return IncrementalEscalationReason.SOURCE_VERSION_CHANGE
    if (
        not candidate.coverage.supports_no_material_change
        or any(
            item.requirement is CoverageRequirement.REQUIRED
            and item.status is not CoverageStatus.COMPLETE
            for item in candidate.coverage.domains
        )
        or any(item.status is not CoverageStatus.COMPLETE for item in candidate.coverage.claims)
        or any(item.status is not CoverageStatus.COMPLETE for item in candidate.coverage.questions)
    ):
        return IncrementalEscalationReason.COVERAGE_INCOMPLETE
    if _semantic_state_payload(candidate.current_state.model_dump(mode="python")) != (
        _semantic_state_payload(baseline.current_state.model_dump(mode="python"))
    ):
        return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
    active_claim_ids = {
        item.id for item in baseline.current_state.claims if item.standing is ClaimStanding.ACTIVE
    }
    reaffirmed_claim_ids = {
        item.object_id
        for item in candidate.delta.claims
        if item.change is ClaimChange.REAFFIRMED
        and item.identity_disposition is IdentityDisposition.EXACT_MATCH
        and item.previous_object_id == item.object_id
    }
    current_question_ids = {
        item.id
        for item in baseline.current_state.questions
        if item.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
    }
    reaffirmed_question_ids = {
        item.object_id
        for item in candidate.delta.questions
        if item.change is QuestionChange.REAFFIRMED
        and item.identity_disposition is IdentityDisposition.EXACT_MATCH
        and item.previous_object_id == item.object_id
    }
    if (
        candidate.delta.opinion_changed
        or candidate.delta.changed_sections
        or reaffirmed_claim_ids != active_claim_ids
        or reaffirmed_question_ids != current_question_ids
    ):
        return IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
    return None


def prepare_experimental_nmc_revision(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
    audit: ResearchUpdateAudit,
) -> ResearchRevisionDraft:
    """Render deterministic NMC summary text and attach the experiment audit."""
    values = {
        "baseline": baseline.cutoff.isoformat(),
        "cutoff": candidate.cutoff.isoformat(),
        "count": len(candidate.delta.new_evidence_refs),
    }
    summaries = {
        "en": (
            "Bounded assessment from {baseline} to {cutoff} found no material change; "
            "the Current Research State was reaffirmed with {count} newly observed "
            "Evidence items."
        ),
        "zh-CN": (
            "从 {baseline} 到 {cutoff} 的有限变化评估未发现重大变化；当前研究状态已重申，"
            "并记录 {count} 条新观察到的证据。"
        ),
        "ja": (
            "{baseline} から {cutoff} までの限定的な変更評価では重要な変更は確認されず、"
            "新たに観測した {count} 件の Evidence とともに現在の Research State を再確認しました。"
        ),
    }
    summary = summaries.get(candidate.current_state.language, summaries["en"]).format(**values)
    updated = candidate.model_copy(
        update={
            "update_summary": candidate.update_summary.model_copy(update={"summary": summary}),
            "research_update_audit": audit,
        }
    )
    return ResearchRevisionDraft.model_validate(updated.model_dump(mode="python"))


def close_revision_over_update_candidate(
    revision: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft | EffectiveEvidenceSnapshot,
) -> ResearchRevisionDraft:
    """Seal bounded candidate Evidence into an authoritative Full Revision snapshot."""
    full_snapshot = revision.evidence_snapshot
    candidate_snapshot = (
        candidate.evidence_snapshot if isinstance(candidate, ResearchRevisionDraft) else candidate
    )
    items = tuple(
        {
            item.ref: item
            for item in (*full_snapshot.bundle.items, *candidate_snapshot.bundle.items)
        }.values()
    )
    tables = tuple(
        {
            item.id: item
            for item in (*full_snapshot.bundle.tables, *candidate_snapshot.bundle.tables)
        }.values()
    )
    lineage = {
        item.evidence_ref: item for item in (*candidate_snapshot.lineage, *full_snapshot.lineage)
    }
    records = tuple(
        {
            item.version_id: item
            for item in (*full_snapshot.source_records, *candidate_snapshot.source_records)
        }.values()
    )
    source_lineage = {
        item.version_id: item
        for item in (
            *candidate_snapshot.source_record_lineage,
            *full_snapshot.source_record_lineage,
        )
    }
    snapshot = EffectiveEvidenceSnapshot(
        bundle=EvidenceBundle(
            instrument=full_snapshot.bundle.instrument,
            analysis_date=full_snapshot.bundle.analysis_date,
            items=items,
            tables=tables,
            sealed_at=full_snapshot.bundle.sealed_at,
        ),
        lineage=tuple(lineage[item.ref] for item in items),
        source_records=records,
        source_record_lineage=tuple(source_lineage[item.version_id] for item in records),
        source_watermarks=tuple(
            {
                (item.source, item.scanned_start, item.scanned_end): item
                for item in (
                    *candidate_snapshot.source_watermarks,
                    *full_snapshot.source_watermarks,
                )
            }.values()
        ),
    )
    return revision.model_copy(update={"evidence_snapshot": snapshot})


class ResearchRevision(ResearchRevisionDraft):
    id: str
    chain_id: str
    sequence: int = Field(ge=1)
    predecessor_revision_id: str | None = None
    producing_run_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime


def derive_next_update_policy(
    revision: ResearchRevisionDraft,
) -> tuple[Literal["incremental_allowed", "full_required"], NextUpdateReason | None]:
    """Derive bounded-update eligibility without conflating role or conclusion."""
    if revision.change_conclusion is ResearchChangeConclusion.INDETERMINATE:
        return "full_required", NextUpdateReason.INDETERMINATE_HEAD
    if (
        not revision.coverage.supports_no_material_change
        or any(
            item.requirement is CoverageRequirement.REQUIRED
            and item.status is not CoverageStatus.COMPLETE
            for item in revision.coverage.domains
        )
        or any(
            item.status is not CoverageStatus.COMPLETE
            for item in (*revision.coverage.claims, *revision.coverage.questions)
        )
    ):
        return "full_required", NextUpdateReason.COVERAGE_INCOMPLETE
    if any(
        item.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY
        for item in revision.delta.change_signals
    ):
        return "full_required", NextUpdateReason.INCOMPATIBLE_MARKET_SEMANTICS
    return "incremental_allowed", None


def derive_shadow_comparison(
    candidate: ResearchRevisionDraft | None,
    authoritative: ResearchRevisionDraft,
) -> Literal["agreement", "disagreement", "inconclusive", "not_applicable"]:
    return derive_shadow_comparison_from_conclusions(
        candidate.change_conclusion if candidate is not None else None,
        authoritative.change_conclusion,
        candidate_present=candidate is not None,
    )


def derive_shadow_comparison_from_conclusions(
    candidate: ResearchChangeConclusion | None,
    authoritative: ResearchChangeConclusion | None,
    *,
    candidate_present: bool,
) -> Literal["agreement", "disagreement", "inconclusive", "not_applicable"]:
    """Compare bounded and authoritative conclusions without duplicating policy."""
    if not candidate_present:
        return "not_applicable"
    if authoritative is ResearchChangeConclusion.INDETERMINATE:
        return "inconclusive"
    if authoritative is ResearchChangeConclusion.NO_MATERIAL_CHANGE:
        return "agreement"
    return "disagreement"


class ResearchChain(ResearchModel):
    id: str
    instrument: str
    is_primary: bool
    current_revision_id: str
    current_revision: ResearchRevision | None = None
    revisions: tuple[ResearchRevision, ...] = ()
    next_update_policy: Literal["incremental_allowed", "full_required"] = "full_required"
    next_update_reason: NextUpdateReason | None = None
    created_at: datetime
    updated_at: datetime


class RevisionExport(ResearchModel):
    schema_version: Literal["1"] = "1"
    chain: ResearchChain
    revision: ResearchRevision
    linked_reports: dict[str, str] = Field(default_factory=dict)


def render_revision_export_markdown(export: RevisionExport) -> str:
    revision = export.revision
    state = revision.current_state
    lines = [
        f"# Research Revision: {state.instrument}",
        "",
        f"- Chain: `{revision.chain_id}`",
        f"- Revision: `{revision.id}`",
        f"- Cutoff: {revision.cutoff.isoformat()}",
        f"- Language: {state.language}",
        f"- Revision role: {revision.role.value}",
        f"- Execution strategy: {revision.execution_strategy.value}",
        "- Change conclusion: "
        + (
            revision.change_conclusion.value
            if revision.change_conclusion is not None
            else "not applicable"
        ),
        "",
        "## Current Research Opinion",
        "",
        f"**{state.opinion.rating.value}** ({state.opinion.confidence.value})",
        "",
        state.opinion.thesis,
        "",
        "## Update Summary",
        "",
        revision.update_summary.summary,
        "",
        "## Research Claims",
        "",
    ]
    for claim in state.claims:
        refs = ", ".join(claim.evidence_refs)
        lines.append(
            f"- `{claim.id}` [{claim.standing.value}/{claim.confidence.value}] "
            f"{claim.statement} (Evidence: {refs})"
        )
    lines.extend(["", "## Research Questions", ""])
    for question in state.questions:
        successor = (
            f"; successor: `{question.successor_question_id}`"
            if question.successor_question_id is not None
            else ""
        )
        evidence_refs = ", ".join(question.evidence_refs) or "none"
        disposition = (
            f"; disposition: {question.last_disposition.value}"
            if question.last_disposition is not None
            else ""
        )
        reason = (
            f"; reason: {question.disposition_reason}"
            if question.disposition_reason is not None
            else ""
        )
        lines.append(
            f"- `{question.id}` [{question.status.value}] {question.question} "
            f"(Evidence: {evidence_refs}{disposition}{reason}{successor})"
        )
    if revision.delta.question_disposition is not None:
        question_audit = revision.delta.question_disposition
        lines.extend(
            [
                "",
                "### Question Disposition Audit",
                "",
                f"- Status: {question_audit.status}",
                f"- Limitation reason: "
                f"{question_audit.limitation_reason.value if question_audit.limitation_reason is not None else 'none'}",
                f"- Repair attempted: {str(question_audit.repair_attempted).lower()}",
            ]
        )
        for item in question_audit.dispositions:
            successor = (
                f"; successor: `{item.successor_question_id}`"
                if item.successor_question_id is not None
                else ""
            )
            candidate = (
                f"; candidate: `{item.candidate_question_id}`"
                if item.candidate_question_id is not None
                else ""
            )
            lines.append(
                f"- `{item.baseline_question_id}`: {item.disposition.value}{candidate}"
                f"{successor}; Evidence: {', '.join(item.evidence_refs)}; {item.reason}"
            )
    lines.extend(["", "## Scenarios", ""])
    for scenario in state.scenarios:
        lines.append(
            f"- **{scenario.kind.value}** ({scenario.likelihood.value}; "
            f"{scenario.horizon}): {scenario.outcome}"
        )
    for title, factors in (
        ("Risks", state.risks),
        ("Catalysts", state.catalysts),
        ("Invalidation Conditions", state.invalidation_conditions),
    ):
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {factor.statement}" for factor in factors)
    lines.extend(["", "## Coverage", ""])
    for domain in revision.coverage.domains:
        limitation = "; ".join(domain.limitations) or "none"
        lines.append(f"- {domain.domain}: {domain.status.value}; limitations: {limitation}")
    lines.extend(["", "### Claim Coverage", ""])
    for item in revision.coverage.claims:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}")
    lines.extend(["", "### Question Coverage", ""])
    for item in revision.coverage.questions:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}")
    lines.extend(["", "## Source Watermarks", ""])
    for watermark in revision.evidence_snapshot.source_watermarks:
        limitation = "; ".join(watermark.limitations) or "none"
        overlap = (
            f"; baseline: {watermark.baseline_cutoff}; overlap starts: {watermark.overlap_start}"
            if watermark.baseline_cutoff is not None
            else ""
        )
        lines.append(
            f"- {watermark.source}: {watermark.scanned_start} to {watermark.scanned_end}; "
            f"status: {watermark.status.value}; returned/reported: "
            f"{watermark.returned_records}/{watermark.reported_records}; "
            f"limitations: {limitation}{overlap}"
        )
    lines.extend(["", "## Source Record Versions", ""])
    source_lineage = {
        item.version_id: item for item in revision.evidence_snapshot.source_record_lineage
    }
    for record in revision.evidence_snapshot.source_records:
        item = source_lineage[record.version_id]
        lines.append(
            f"- `{record.version_id}` ({record.source} `{record.record_id}`): "
            f"{record.status.value}; {item.lineage}; observed now: "
            f"{str(item.observed_in_execution).lower()}; available: "
            f"{record.available_at.isoformat()}"
            f" ({record.availability_basis or 'source timestamp'}); native record: "
            f"{record.native_record_id or 'not recorded'}; adjustment: "
            f"{record.adjustment or 'not applicable'}; unit/precision: "
            f"{record.unit or 'not recorded'}/{record.precision if record.precision is not None else 'not recorded'}; "
            f"fallback: {str(record.fallback).lower()}; {record.title}"
        )
    lines.extend(["", "## Fundamental and Market Change Signals", ""])
    for signal in revision.delta.change_signals:
        values = (
            f"; values: {signal.previous_value} -> {signal.current_value}"
            if signal.previous_value is not None or signal.current_value is not None
            else ""
        )
        boundary = (
            f"; boundary: {signal.boundary_label} ({signal.boundary_value})"
            if signal.boundary_label is not None
            else ""
        )
        lines.append(
            f"- `{signal.kind.value}` [{signal.domain}] `{signal.record_id}`; "
            f"requires Full Analysis: {str(signal.requires_full_analysis).lower()}"
            f"{values}{boundary}; {signal.detail}"
        )
    if revision.research_update_audit is not None:
        audit = revision.research_update_audit
        lines.extend(
            [
                "",
                "## Bounded Update Finding",
                "",
                f"- Mode: {audit.mode}",
                f"- Candidate Change Conclusion: "
                f"{audit.candidate.change_conclusion if audit.candidate is not None else 'none'}",
                f"- Authoritative strategy: {audit.authoritative_strategy}",
                f"- Escalation reason: {audit.escalation_reason or 'none'}",
                f"- Comparison: {audit.comparison}",
                *(
                    [
                        "- Comparison explanation: the authoritative Full reassessment was "
                        "Indeterminate, so this result is counted as neither agreement nor "
                        "disagreement."
                    ]
                    if audit.comparison == "inconclusive"
                    else []
                ),
                "- Bounded checked windows: "
                + (
                    "; ".join(
                        f"{item.source} {item.scanned_start} to {item.scanned_end} ({item.status})"
                        for item in audit.checked_windows
                    )
                    or "none"
                ),
                "- Bounded Evidence lineage: "
                + (
                    ", ".join(
                        f"{item.evidence_ref}:{item.lineage}" for item in audit.evidence_lineage
                    )
                    or "none"
                ),
                f"- Bounded work: {audit.bounded_metrics.llm_calls} LLM calls, "
                f"{audit.bounded_metrics.tool_calls} tool calls, "
                f"{audit.bounded_metrics.input_tokens}/"
                f"{audit.bounded_metrics.output_tokens} input/output tokens, "
                f"cost: {audit.bounded_metrics.cost_usd if audit.bounded_metrics.cost_usd is not None else 'not reported'}, "
                f"{audit.bounded_metrics.wall_time_seconds:.3f}s",
                f"- Full work: {audit.full_metrics.llm_calls} LLM calls, "
                f"{audit.full_metrics.tool_calls} tool calls, "
                f"{audit.full_metrics.input_tokens}/"
                f"{audit.full_metrics.output_tokens} input/output tokens, "
                f"cost: {audit.full_metrics.cost_usd if audit.full_metrics.cost_usd is not None else 'not reported'}, "
                f"{audit.full_metrics.wall_time_seconds:.3f}s",
            ]
        )
        if audit.semantic_assessment is not None:
            lines.extend(
                [
                    "",
                    "### Semantic Change Assessment",
                    "",
                    f"- Language: {audit.semantic_assessment.language}",
                    f"- Summary: {audit.semantic_assessment.summary}",
                ]
            )
            for relationship in audit.semantic_assessment.relationships:
                targets = (
                    *relationship.suggested_claim_ids,
                    *relationship.suggested_question_ids,
                )
                lines.append(
                    f"- `{relationship.relationship}`; targets: "
                    f"{', '.join(targets) or 'none'}; Evidence: "
                    f"{', '.join(relationship.evidence_refs)}"
                )
    lines.extend(["", "## Effective Evidence Snapshot", ""])
    lineage = {item.evidence_ref: item for item in revision.evidence_snapshot.lineage}
    for evidence_item in revision.evidence_snapshot.bundle.items:
        item_lineage = lineage[evidence_item.ref]
        lines.extend(
            [
                f"### `{evidence_item.ref}`",
                "",
                f"- Lineage: {item_lineage.lineage}",
                f"- Source: {evidence_item.source}",
                f"- Type: {evidence_item.evidence_type}",
                f"- Requested date: {evidence_item.requested_date.isoformat()}",
                f"- Effective date: {evidence_item.effective_date or 'not recorded'}",
                f"- Available at: {evidence_item.available_at or 'not recorded'}",
                "",
                evidence_item.content
                or (
                    str(evidence_item.value)
                    if evidence_item.value is not None
                    else "No readable content recorded."
                ),
                "",
            ]
        )
    metrics = revision.metrics
    lines.extend(
        [
            "## Execution Metrics",
            "",
            f"- LLM calls: {metrics.llm_calls}",
            f"- Tool calls: {metrics.tool_calls}",
            f"- Input tokens: {metrics.input_tokens}",
            f"- Output tokens: {metrics.output_tokens}",
            f"- Cache-hit input tokens: {metrics.cache_hit_input_tokens}",
            f"- Cache-miss input tokens: {metrics.cache_miss_input_tokens}",
            f"- Reasoning output tokens: {metrics.reasoning_output_tokens}",
            f"- Wall time seconds: {metrics.wall_time_seconds}",
        ]
    )
    if export.linked_reports:
        lines.extend(["", "## Linked Full Reports", ""])
        for role, markdown in export.linked_reports.items():
            lines.extend([f"### {role.title()}", "", markdown, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_revision_export_package(export: RevisionExport) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "revision.json",
            export.model_dump_json(indent=2),
        )
        archive.writestr("revision.md", render_revision_export_markdown(export))
        archive.writestr(
            "evidence.json",
            json.dumps(
                export.revision.evidence_snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
        )
    return output.getvalue()


def _claim_confidence(value: float | None) -> ClaimConfidence:
    if value is None:
        return ClaimConfidence.INDETERMINATE
    if value >= 0.75:
        return ClaimConfidence.HIGH
    if value >= 0.5:
        return ClaimConfidence.MEDIUM
    return ClaimConfidence.LOW


def _decision_confidence(value: float | None) -> DecisionConfidence:
    return DecisionConfidence(_claim_confidence(value).value)


def _new_claim_id() -> str:
    return f"claim_{uuid4().hex}"


def _new_question_id() -> str:
    return f"question_{uuid4().hex}"


def _identity_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _claim_identity(claim: ResearchClaim) -> tuple[str, EpistemicKind, DecisionRole]:
    return (_identity_text(claim.statement), claim.epistemic_kind, claim.decision_role)


def _question_identity(question: ResearchQuestion) -> str:
    return _identity_text(question.question)


def _unique_identity_matches(current, candidate, key):
    current_by_key: dict[object, list[object]] = {}
    candidate_by_key: dict[object, list[object]] = {}
    for item in current:
        current_by_key.setdefault(key(item), []).append(item)
    for item in candidate:
        candidate_by_key.setdefault(key(item), []).append(item)
    matches = {}
    ambiguous = set()
    for identity, candidate_items in candidate_by_key.items():
        current_items = current_by_key.get(identity, [])
        if len(candidate_items) == len(current_items) == 1:
            matches[candidate_items[0].id] = current_items[0]
        elif current_items:
            ambiguous.update(item.id for item in candidate_items)
    return matches, ambiguous


def _source_metadata(
    bundle: EvidenceBundle,
) -> tuple[tuple[SourceRecordVersion, ...], tuple[SourceWatermarkSnapshot, ...]]:
    records: dict[str, SourceRecordVersion] = {}
    watermarks: dict[tuple[str, date, date], SourceWatermarkSnapshot] = {}
    status_rank = {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.LIMITED: 1,
        CoverageStatus.UNAVAILABLE: 2,
    }
    cutoff_timezone = market_timezone(bundle.instrument)
    for evidence in bundle.items:
        for raw in evidence.provenance.get("source_records", ()):
            record = SourceRecordVersion.model_validate(
                {
                    **raw,
                    "evidence_ref": evidence.ref,
                    "fallback": evidence.fallback,
                }
            )
            if record.available_at.astimezone(cutoff_timezone).date() > bundle.analysis_date:
                raise ValueError("Source Record Version is available after the analysis cutoff")
            existing = records.get(record.version_id)
            if existing is not None:
                if existing.model_dump(exclude={"evidence_ref"}) != record.model_dump(
                    exclude={"evidence_ref"}
                ):
                    raise ValueError("Source Record Version identity has conflicting observations")
                record = existing
            records[record.version_id] = record
        for raw in evidence.provenance.get("source_watermarks", ()):
            watermark = SourceWatermarkSnapshot.model_validate(raw)
            key = (watermark.source, watermark.scanned_start, watermark.scanned_end)
            existing = watermarks.get(key)
            if existing is None:
                watermarks[key] = watermark
                continue
            worse = max((existing, watermark), key=lambda item: status_rank[item.status])
            reported_values = tuple(
                value
                for value in (existing.reported_records, watermark.reported_records)
                if value is not None
            )
            watermarks[key] = SourceWatermarkSnapshot(
                source=watermark.source,
                scanned_start=watermark.scanned_start,
                scanned_end=watermark.scanned_end,
                status=worse.status,
                temporal_scope=(
                    existing.temporal_scope
                    if existing.temporal_scope == watermark.temporal_scope
                    else "unknown"
                ),
                limitations=tuple(dict.fromkeys((*existing.limitations, *watermark.limitations))),
                returned_records=max(existing.returned_records, watermark.returned_records),
                reported_records=max(reported_values) if reported_values else None,
            )
    return tuple(records.values()), tuple(watermarks.values())


def _source_coverage(
    state: CurrentResearchState,
    records: tuple[SourceRecordVersion, ...],
    watermarks: tuple[SourceWatermarkSnapshot, ...],
    *,
    status_blocking_records: tuple[SourceRecordVersion, ...] | None = None,
    required_data_domains: tuple[str, ...] = (),
) -> tuple[tuple[ResearchDomainCoverage, ...], bool]:
    explicitly_required = {
        source
        for claim in state.claims
        if claim.standing is ClaimStanding.ACTIVE
        for source in claim.required_sources
    }
    explicitly_required.update(
        source
        for question in state.questions
        if question.status is QuestionStatus.OPEN
        for source in question.required_sources
    )
    refs_by_source: dict[str, list[str]] = {}
    for record in records:
        refs_by_source.setdefault(record.source, []).append(record.evidence_ref)
    domains = []
    supports_quiet = True
    watermarks_by_source: dict[str, list[SourceWatermarkSnapshot]] = {}
    for watermark in watermarks:
        watermarks_by_source.setdefault(watermark.source, []).append(watermark)
    observed_sources = set(watermarks_by_source)
    status_rank = {
        CoverageStatus.COMPLETE: 0,
        CoverageStatus.LIMITED: 1,
        CoverageStatus.UNAVAILABLE: 2,
    }
    for source_watermarks in watermarks_by_source.values():
        watermark = max(source_watermarks, key=lambda item: status_rank[item.status])
        domain_name = {
            "Google News": "media_news",
            "J-Quants fundamentals": "fundamentals",
            "J-Quants adjusted OHLCV": "market",
        }.get(watermark.source, "company_disclosures")
        required = (
            watermark.source in explicitly_required
            or (state.instrument.endswith(".T") and watermark.source in {"EDINET", "TDnet"})
            or domain_name in required_data_domains
        )
        advisory = not required
        requirement = CoverageRequirement.ADVISORY if advisory else CoverageRequirement.REQUIRED
        live_only_required = requirement is CoverageRequirement.REQUIRED and any(
            item.temporal_scope != "point_in_time" for item in source_watermarks
        )
        ordered_intervals = sorted(
            (item.scanned_start, item.scanned_end) for item in source_watermarks
        )
        missing_interval = False
        covered_end = ordered_intervals[0][1]
        for current_start, current_end in ordered_intervals[1:]:
            if current_start > covered_end + timedelta(days=1):
                missing_interval = True
                break
            covered_end = max(covered_end, current_end)
        if requirement is CoverageRequirement.REQUIRED and (
            watermark.status is not CoverageStatus.COMPLETE
            or live_only_required
            or missing_interval
        ):
            supports_quiet = False
        limitations = (
            tuple(dict.fromkeys(value for item in source_watermarks for value in item.limitations))
            + (("Required source coverage is not point-in-time.",) if live_only_required else ())
            + (
                ("Source watermark intervals contain an unscanned gap.",)
                if missing_interval
                else ()
            )
        )
        domains.append(
            ResearchDomainCoverage(
                domain=domain_name,
                source=watermark.source,
                requirement=requirement,
                status=(
                    CoverageStatus.LIMITED
                    if (live_only_required or missing_interval)
                    and watermark.status is CoverageStatus.COMPLETE
                    else watermark.status
                ),
                evidence_refs=tuple(dict.fromkeys(refs_by_source.get(watermark.source, ()))),
                limitations=limitations,
            )
        )
    if state.instrument.endswith(".T"):
        for source in ("EDINET", "TDnet"):
            if source in observed_sources:
                continue
            supports_quiet = False
            domains.append(
                ResearchDomainCoverage(
                    domain="company_disclosures",
                    source=source,
                    requirement=CoverageRequirement.REQUIRED,
                    status=CoverageStatus.UNAVAILABLE,
                    limitations=(f"{source} collection coverage was not recorded.",),
                )
            )
        required_sources = {
            "fundamentals": "J-Quants fundamentals",
            "market": "J-Quants adjusted OHLCV",
        }
        for domain_name, source in required_sources.items():
            if domain_name not in required_data_domains or source in observed_sources:
                continue
            supports_quiet = False
            domains.append(
                ResearchDomainCoverage(
                    domain=domain_name,
                    source=source,
                    requirement=CoverageRequirement.REQUIRED,
                    status=CoverageStatus.UNAVAILABLE,
                    limitations=(f"{source} collection coverage was not recorded.",),
                )
            )
    for source in sorted(explicitly_required - observed_sources):
        if source in {"EDINET", "TDnet"} and state.instrument.endswith(".T"):
            continue
        supports_quiet = False
        domains.append(
            ResearchDomainCoverage(
                domain="required_source",
                source=source,
                requirement=CoverageRequirement.REQUIRED,
                status=CoverageStatus.UNAVAILABLE,
                limitations=(f"Required {source} collection coverage was not recorded.",),
            )
        )
    if any(
        record.status is not SourceRecordStatus.PUBLISHED and record.source in {"EDINET", "TDnet"}
        for record in (records if status_blocking_records is None else status_blocking_records)
    ):
        supports_quiet = False
    return tuple(domains), supports_quiet


def _crossed(previous: float, current: float, boundary: float) -> bool:
    return (previous < boundary <= current) or (previous > boundary >= current)


def _change_signals(
    baseline: ResearchRevisionDraft,
    candidate_records: tuple[SourceRecordVersion, ...],
) -> tuple[ResearchChangeSignal, ...]:
    """Compare producer-owned snapshots without parsing analyst prose."""
    baseline_by_record: dict[str, list[SourceRecordVersion]] = {}
    for record in baseline.evidence_snapshot.source_records:
        key = (
            record.comparison_key
            if record.record_kind is SourceRecordKind.FUNDAMENTAL
            else record.record_id
        )
        baseline_by_record.setdefault(key or record.record_id, []).append(record)
    candidate_by_record: dict[str, list[SourceRecordVersion]] = {}
    for record in candidate_records:
        if record.record_kind is not SourceRecordKind.DISCLOSURE:
            key = (
                record.comparison_key
                if record.record_kind is SourceRecordKind.FUNDAMENTAL
                else record.record_id
            )
            candidate_by_record.setdefault(key or record.record_id, []).append(record)

    signals: list[ResearchChangeSignal] = []
    for comparison_key, current_versions in candidate_by_record.items():
        previous_versions = baseline_by_record.get(comparison_key, [])
        previous_ids = {item.version_id for item in previous_versions}
        new_versions = [item for item in current_versions if item.version_id not in previous_ids]
        record_kind = current_versions[-1].record_kind
        record_id = current_versions[-1].record_id
        if not new_versions:
            previous = previous_versions[-1] if previous_versions else None
            current = current_versions[-1]
            signals.append(
                ResearchChangeSignal(
                    kind=ResearchChangeKind.UNCHANGED_OBSERVATION,
                    domain=(
                        "fundamentals" if record_kind is SourceRecordKind.FUNDAMENTAL else "market"
                    ),
                    record_id=record_id,
                    previous_version_id=previous.version_id if previous else None,
                    current_version_id=current.version_id,
                    requires_full_analysis=False,
                    detail="The source-native version was re-observed without change.",
                    previous_value=previous.observation_value if previous else None,
                    current_value=current.observation_value,
                )
            )
            continue

        if record_kind is SourceRecordKind.FUNDAMENTAL:
            known = {item.version_id: item for item in (*previous_versions, *current_versions)}
            for current in new_versions:
                previous = known.get(current.replaces_version_id or "")
                if previous is None and previous_versions:
                    previous = previous_versions[-1]
                if previous is None:
                    kind = ResearchChangeKind.NEW_FUNDAMENTAL_FILING
                    detail = "A new official fundamental reporting period was observed."
                elif current.change_hint == "accounting_scope_change" or (
                    previous is not None and current.accounting_scope != previous.accounting_scope
                ):
                    kind = ResearchChangeKind.ACCOUNTING_SCOPE_CHANGE
                    detail = "The accounting standard or consolidation scope changed."
                elif current.change_hint == "restatement":
                    kind = ResearchChangeKind.FUNDAMENTAL_RESTATEMENT
                    detail = "Previously reported fundamental values were restated."
                elif current.change_hint == "correction" or (
                    current.status is SourceRecordStatus.CORRECTED
                ):
                    kind = ResearchChangeKind.FUNDAMENTAL_CORRECTION
                    detail = "An official correction changed the observed version."
                else:
                    kind = ResearchChangeKind.UNCLASSIFIABLE_FUNDAMENTAL_CHANGE
                    detail = "The fundamental snapshot changed without a safe classification."
                signals.append(
                    ResearchChangeSignal(
                        kind=kind,
                        domain="fundamentals",
                        record_id=record_id,
                        previous_version_id=previous.version_id if previous else None,
                        current_version_id=current.version_id,
                        requires_full_analysis=True,
                        detail=detail,
                    )
                )
            continue

        previous = previous_versions[-1] if previous_versions else None
        current = new_versions[-1]
        incompatible = previous is None or any(
            (
                previous.source != current.source,
                previous.adjustment != current.adjustment,
                previous.unit != current.unit,
            )
        )
        if incompatible:
            signals.append(
                ResearchChangeSignal(
                    kind=ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY,
                    domain="market",
                    record_id=record_id,
                    previous_version_id=previous.version_id if previous else None,
                    current_version_id=current.version_id,
                    requires_full_analysis=True,
                    detail="Provider, adjustment, or unit semantics are not baseline-compatible.",
                    previous_value=previous.observation_value if previous else None,
                    current_value=current.observation_value,
                )
            )
            continue
        crossing = next(
            (
                boundary
                for boundary in baseline.current_state.market_reference_levels
                if previous.observation_value is not None
                and current.observation_value is not None
                and boundary.unit == current.unit
                and _crossed(previous.observation_value, current.observation_value, boundary.value)
            ),
            None,
        )
        signals.append(
            ResearchChangeSignal(
                kind=(
                    ResearchChangeKind.MARKET_BOUNDARY_CROSSING
                    if crossing is not None
                    else ResearchChangeKind.ORDINARY_MARKET_MOVE
                ),
                domain="market",
                record_id=record_id,
                previous_version_id=previous.version_id,
                current_version_id=current.version_id,
                requires_full_analysis=crossing is not None,
                detail=(
                    "The observed market value crossed a thesis-relevant reference."
                    if crossing is not None
                    else "The market value changed without crossing a recorded boundary."
                ),
                boundary_label=crossing.label if crossing is not None else None,
                boundary_value=crossing.value if crossing is not None else None,
                previous_value=previous.observation_value,
                current_value=current.observation_value,
            )
        )
    return tuple(signals)


def assess_deterministic_update(
    baseline_revision_id: str,
    baseline: ResearchRevisionDraft,
    request: AnalysisRequest,
    evidence: EvidenceBundle,
    *,
    metrics: RunMetrics | None = None,
) -> IncrementalGateResult:
    """Apply fail-closed gates and build a quiet bounded-update candidate."""

    if (
        derive_next_update_policy(baseline)[0] != "incremental_allowed"
        or request.ticker != baseline.current_state.instrument
        or request.analysis_date <= baseline.cutoff
        or evidence.instrument != request.ticker
        or evidence.analysis_date != request.analysis_date
    ):
        return IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.INVALID_BASELINE,
            metrics=metrics or RunMetrics(),
        )
    try:
        candidate_records, candidate_watermarks = _source_metadata(evidence)
        baseline_records = {
            item.version_id: item for item in baseline.evidence_snapshot.source_records
        }
        newly_observed = tuple(
            item for item in candidate_records if item.version_id not in baseline_records
        )
        state_refs = {
            ref
            for claim in baseline.current_state.claims
            if claim.standing is ClaimStanding.ACTIVE
            for ref in claim.evidence_refs
        }
        state_refs.update(
            ref
            for question in baseline.current_state.questions
            if question.status is QuestionStatus.OPEN
            for ref in question.evidence_refs
        )
        required_domains = {
            (
                "fundamentals"
                if record.record_kind is SourceRecordKind.FUNDAMENTAL
                else "market"
                if record.record_kind is SourceRecordKind.MARKET
                else "company_disclosures"
            )
            for record in baseline.evidence_snapshot.source_records
            if record.evidence_ref in state_refs
        }
        if baseline.current_state.market_reference_levels:
            required_domains.add("market")
        watermarks = tuple(
            item.model_copy(
                update={
                    "baseline_cutoff": baseline.cutoff,
                    "overlap_start": (
                        item.scanned_start
                        if item.scanned_start <= baseline.cutoff <= item.scanned_end
                        else None
                    ),
                    "status": (
                        CoverageStatus.LIMITED
                        if (
                            not item.scanned_start <= baseline.cutoff <= item.scanned_end
                            or item.scanned_end != request.analysis_date
                        )
                        and item.status is CoverageStatus.COMPLETE
                        else item.status
                    ),
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *item.limitations,
                                *(
                                    ()
                                    if item.scanned_start <= baseline.cutoff <= item.scanned_end
                                    else (
                                        "Collection window did not overlap the Eligible Baseline cutoff.",
                                    )
                                ),
                                *(
                                    ()
                                    if item.scanned_end == request.analysis_date
                                    else ("Collection window did not end at the update cutoff.",)
                                ),
                            )
                        )
                    ),
                }
            )
            for item in candidate_watermarks
        )
        combined_records = tuple(
            {
                item.version_id: item for item in (*baseline_records.values(), *candidate_records)
            }.values()
        )
        domains, supports_quiet = _source_coverage(
            baseline.current_state,
            combined_records,
            watermarks,
            status_blocking_records=newly_observed,
            required_data_domains=tuple(sorted(required_domains)),
        )
        signals = _change_signals(baseline, candidate_records)
        reason = None
        status_reasons = {
            SourceRecordStatus.CORRECTED: IncrementalEscalationReason.SOURCE_CORRECTION,
            SourceRecordStatus.WITHDRAWN: IncrementalEscalationReason.SOURCE_WITHDRAWAL,
            SourceRecordStatus.REPLACED: IncrementalEscalationReason.SOURCE_REPLACEMENT,
        }
        for record in newly_observed:
            if record.status in status_reasons:
                reason = status_reasons[record.status]
                break
        if reason is None:
            for signal in signals:
                if signal.kind is ResearchChangeKind.MARKET_SEMANTIC_INCOMPATIBILITY:
                    reason = IncrementalEscalationReason.INCOMPATIBLE_SEMANTICS
                    break
                if signal.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING:
                    reason = IncrementalEscalationReason.THRESHOLD_CROSSING
                    break
                if signal.requires_full_analysis:
                    reason = IncrementalEscalationReason.SOURCE_VERSION_CHANGE
                    break
        if reason is None and any(
            item.record_kind is SourceRecordKind.DISCLOSURE for item in newly_observed
        ):
            reason = IncrementalEscalationReason.SOURCE_VERSION_CHANGE
        if reason is None and not supports_quiet:
            reason = IncrementalEscalationReason.COVERAGE_INCOMPLETE
        baseline_bundle = baseline.evidence_snapshot.bundle
        new_refs = {item.ref for item in evidence.items}
        combined_items = tuple(
            {item.ref: item for item in (*baseline_bundle.items, *evidence.items)}.values()
        )
        combined_tables = tuple(
            {item.id: item for item in (*baseline_bundle.tables, *evidence.tables)}.values()
        )
        bundle = EvidenceBundle(
            instrument=request.ticker,
            analysis_date=request.analysis_date,
            items=combined_items,
            tables=combined_tables,
            sealed_at=evidence.sealed_at,
        )
        evidence_snapshot = EffectiveEvidenceSnapshot(
            bundle=bundle,
            lineage=tuple(
                EvidenceSnapshotItem(
                    evidence_ref=item.ref,
                    lineage="new" if item.ref in new_refs else "inherited",
                    source_revision_id=(None if item.ref in new_refs else baseline_revision_id),
                )
                for item in combined_items
            ),
            source_records=combined_records,
            source_record_lineage=tuple(
                SourceRecordSnapshotItem(
                    version_id=item.version_id,
                    lineage=("new" if item.version_id not in baseline_records else "inherited"),
                    observed_in_execution=item.version_id
                    in {record.version_id for record in candidate_records},
                    source_revision_id=(
                        None if item.version_id not in baseline_records else baseline_revision_id
                    ),
                )
                for item in combined_records
            ),
            source_watermarks=watermarks,
        )
        coverage_status = CoverageStatus.COMPLETE if supports_quiet else CoverageStatus.LIMITED
        limitations = tuple(
            dict.fromkeys(value for domain in domains for value in domain.limitations)
        )
        coverage = CoverageAttestation(
            claims=tuple(
                ResearchObjectCoverage(
                    object_id=item.id,
                    status=coverage_status,
                    evidence_refs=item.evidence_refs,
                )
                for item in baseline.current_state.claims
            ),
            questions=tuple(
                ResearchObjectCoverage(
                    object_id=item.id,
                    status=coverage_status,
                )
                for item in baseline.current_state.questions
            ),
            domains=domains,
            limitations=limitations,
            supports_no_material_change=supports_quiet,
        )
        if reason is not None:
            return IncrementalGateResult(
                escalation_reason=reason,
                coverage=coverage,
                evidence_snapshot=evidence_snapshot,
                metrics=metrics or RunMetrics(),
            )
        candidate = ResearchRevisionDraft(
            cutoff=request.analysis_date,
            role=ResearchRevisionRole.UPDATE,
            execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
            change_conclusion=ResearchChangeConclusion.NO_MATERIAL_CHANGE,
            delta=RevisionDelta(
                opinion_changed=False,
                claims=tuple(
                    ClaimRevisionDelta(
                        object_id=item.id,
                        previous_object_id=item.id,
                        change=ClaimChange.REAFFIRMED,
                        identity_disposition=IdentityDisposition.EXACT_MATCH,
                    )
                    for item in baseline.current_state.claims
                    if item.standing is ClaimStanding.ACTIVE
                ),
                questions=tuple(
                    QuestionRevisionDelta(
                        object_id=item.id,
                        previous_object_id=item.id,
                        change=QuestionChange.REAFFIRMED,
                        identity_disposition=IdentityDisposition.EXACT_MATCH,
                    )
                    for item in baseline.current_state.questions
                    if item.status in {QuestionStatus.OPEN, QuestionStatus.ANSWERED}
                ),
                inherited_evidence_refs=tuple(
                    item.ref for item in baseline_bundle.items if item.ref not in new_refs
                ),
                new_evidence_refs=tuple(item.ref for item in evidence.items),
                change_signals=signals,
            ),
            current_state=baseline.current_state.model_copy(
                update={
                    "cutoff": request.analysis_date,
                    "evidence_refs": tuple(item.ref for item in combined_items),
                    "scenarios": tuple(
                        item.model_copy(update={"cutoff": request.analysis_date})
                        for item in baseline.current_state.scenarios
                    ),
                }
            ),
            coverage=coverage,
            update_summary=UpdateSummary(
                language=baseline.current_state.language,
                summary=(
                    "Deterministic gates found no material change; the bounded candidate "
                    "is ready for the configured research-update mode."
                ),
                checked_domains=tuple(dict.fromkeys(item.domain for item in domains)),
                limitations=limitations,
                baseline_cutoff=baseline.cutoff,
                analysis_cutoff=request.analysis_date,
                execution_strategy=ResearchExecutionStrategy.INCREMENTAL,
                change_conclusion=ResearchChangeConclusion.NO_MATERIAL_CHANGE,
                new_evidence_refs=tuple(item.ref for item in evidence.items),
            ),
            evidence_snapshot=evidence_snapshot,
        )
    except (TypeError, ValueError):
        return IncrementalGateResult(
            escalation_reason=IncrementalEscalationReason.SCHEMA_INVALID,
            metrics=metrics or RunMetrics(),
        )
    return IncrementalGateResult(
        candidate=candidate,
        coverage=coverage,
        evidence_snapshot=evidence_snapshot,
        metrics=metrics or RunMetrics(),
    )


def assemble_full_update(
    baseline_revision_id: str,
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
) -> ResearchRevisionDraft:
    """Compare an independently assembled Full result with an Eligible Baseline."""
    if candidate.current_state.instrument != baseline.current_state.instrument:
        raise ValueError("update Instrument must match the Eligible Baseline")
    if candidate.cutoff <= baseline.cutoff:
        raise ValueError("update cutoff must be strictly later than the Eligible Baseline")

    claim_matches, ambiguous_claims = _unique_identity_matches(
        baseline.current_state.claims,
        candidate.current_state.claims,
        _claim_identity,
    )
    question_matches, ambiguous_questions = _unique_identity_matches(
        baseline.current_state.questions,
        candidate.current_state.questions,
        _question_identity,
    )
    independently_introduced_question_ids = {
        item.id
        for item in candidate.current_state.questions
        if item.id not in question_matches and item.id not in ambiguous_questions
    }
    question_disposition = candidate.delta.question_disposition
    if baseline.current_state.questions and question_disposition is None:
        question_disposition = QuestionDispositionAudit(
            status="limited",
            language=candidate.current_state.language,
            limitation_reason=QuestionDispositionLimitation.INCOMPLETE,
        )
    if question_disposition is not None and question_disposition.status == "complete":
        baseline_questions_by_id = {item.id: item for item in baseline.current_state.questions}
        question_matches = {
            item.candidate_question_id: baseline_questions_by_id[item.baseline_question_id]
            for item in question_disposition.dispositions
            if item.candidate_question_id is not None
        }
        ambiguous_questions = set()
    elif baseline.current_state.questions:
        question_matches = {}
        ambiguous_questions = set()
    claim_ids = {candidate_id: previous.id for candidate_id, previous in claim_matches.items()}
    question_ids = {
        candidate_id: previous.id for candidate_id, previous in question_matches.items()
    }

    claims = tuple(
        claim.model_copy(
            update={
                "id": claim_ids.get(claim.id, claim.id),
                "required_sources": (
                    claim_matches[claim.id].required_sources
                    if claim.id in claim_matches
                    else claim.required_sources
                ),
            }
        )
        for claim in candidate.current_state.claims
    )
    questions = tuple(
        question.model_copy(
            update={
                "id": question_ids.get(question.id, question.id),
                "required_sources": (
                    question_matches[question.id].required_sources
                    if question.id in question_matches
                    else question.required_sources
                ),
            }
        )
        for question in candidate.current_state.questions
    )
    retained_claim_ids = {claim.id for claim in claims}
    retained_question_ids = {question.id for question in questions}
    retired_claims = tuple(
        claim.model_copy(update={"standing": ClaimStanding.RETIRED})
        for claim in baseline.current_state.claims
        if claim.id not in retained_claim_ids
    )
    retired_questions = tuple(
        question
        for question in baseline.current_state.questions
        if question.id not in retained_question_ids
    )
    if question_disposition is not None and question_disposition.status == "complete":
        candidate_questions_by_id = {item.id: item for item in candidate.current_state.questions}
        baseline_questions_by_id = {item.id: item for item in baseline.current_state.questions}
        assigned_candidate_ids = {
            value
            for item in question_disposition.dispositions
            for value in (item.candidate_question_id, item.successor_question_id)
            if value is not None
        }
        disposed_questions: list[ResearchQuestion] = []
        for item in question_disposition.dispositions:
            previous = baseline_questions_by_id[item.baseline_question_id]
            source = (
                candidate_questions_by_id[item.candidate_question_id]
                if item.candidate_question_id is not None
                else previous
            )
            disposed_questions.append(
                source.model_copy(
                    update={
                        "id": previous.id,
                        "status": _status_after_question_disposition(
                            item.disposition, previous.status
                        ),
                        "evidence_refs": tuple(
                            dict.fromkeys((*source.evidence_refs, *item.evidence_refs))
                        ),
                        "required_sources": previous.required_sources,
                        "successor_question_id": item.successor_question_id,
                        "last_disposition": item.disposition,
                        "disposition_reason": item.reason,
                    }
                )
            )
        questions = (
            *disposed_questions,
            *(
                item
                for item in candidate.current_state.questions
                if item.id not in assigned_candidate_ids
            ),
            *(
                candidate_questions_by_id[item.successor_question_id]
                for item in question_disposition.dispositions
                if item.successor_question_id is not None
            ),
        )
        retained_question_ids = {question.id for question in questions}
        retired_questions = ()

    def remap_claim_ids(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(claim_ids.get(value, value) for value in values)

    opinion = candidate.current_state.opinion.model_copy(
        update={
            "primary_claim_ids": remap_claim_ids(candidate.current_state.opinion.primary_claim_ids)
        }
    )
    scenarios = tuple(
        item.model_copy(update={"assumption_claim_ids": remap_claim_ids(item.assumption_claim_ids)})
        for item in candidate.current_state.scenarios
    )

    def remap_factors(values: tuple[ResearchFactor, ...]) -> tuple[ResearchFactor, ...]:
        return tuple(
            item.model_copy(update={"claim_ids": remap_claim_ids(item.claim_ids)})
            for item in values
        )

    baseline_bundle = baseline.evidence_snapshot.bundle
    candidate_bundle = candidate.evidence_snapshot.bundle
    new_refs = {item.ref for item in candidate_bundle.items}
    combined_items = tuple(
        {item.ref: item for item in (*baseline_bundle.items, *candidate_bundle.items)}.values()
    )
    combined_tables = tuple(
        {item.id: item for item in (*baseline_bundle.tables, *candidate_bundle.tables)}.values()
    )
    combined_bundle = EvidenceBundle(
        instrument=candidate_bundle.instrument,
        analysis_date=candidate_bundle.analysis_date,
        items=combined_items,
        tables=combined_tables,
        sealed_at=candidate_bundle.sealed_at,
    )
    baseline_versions = {
        item.version_id: item for item in baseline.evidence_snapshot.source_records
    }
    candidate_versions = {
        item.version_id: item for item in candidate.evidence_snapshot.source_records
    }
    combined_versions = tuple((baseline_versions | candidate_versions).values())
    current_version_ids = set(candidate_versions)
    source_record_lineage = tuple(
        SourceRecordSnapshotItem(
            version_id=item.version_id,
            lineage="inherited" if item.version_id in baseline_versions else "new",
            observed_in_execution=item.version_id in current_version_ids,
            source_revision_id=(
                baseline_revision_id if item.version_id in baseline_versions else None
            ),
        )
        for item in combined_versions
    )
    source_watermarks = []
    for item in candidate.evidence_snapshot.source_watermarks:
        covers_baseline = item.scanned_start <= baseline.cutoff <= item.scanned_end
        overlap_limitation = (
            ()
            if covers_baseline
            else ("Collection window did not overlap the Eligible Baseline cutoff.",)
        )
        source_watermarks.append(
            item.model_copy(
                update={
                    "baseline_cutoff": baseline.cutoff,
                    "overlap_start": item.scanned_start if covers_baseline else None,
                    "status": (
                        CoverageStatus.LIMITED
                        if overlap_limitation and item.status is CoverageStatus.COMPLETE
                        else item.status
                    ),
                    "limitations": tuple(dict.fromkeys((*item.limitations, *overlap_limitation))),
                }
            )
        )
    source_watermarks = tuple(source_watermarks)
    inherited_refs = tuple(item.ref for item in baseline_bundle.items if item.ref not in new_refs)
    state = candidate.current_state.model_copy(
        update={
            "opinion": opinion,
            "claims": (*claims, *retired_claims),
            "questions": (*questions, *retired_questions),
            "scenarios": scenarios,
            "risks": remap_factors(candidate.current_state.risks),
            "catalysts": remap_factors(candidate.current_state.catalysts),
            "invalidation_conditions": remap_factors(
                candidate.current_state.invalidation_conditions
            ),
            "evidence_refs": tuple(item.ref for item in combined_items),
        }
    )
    newly_observed_versions = tuple(
        item
        for version_id, item in candidate_versions.items()
        if version_id not in baseline_versions
    )
    change_signals = _change_signals(baseline, candidate.evidence_snapshot.source_records)
    source_domains, supports_quiet = _source_coverage(
        state,
        combined_versions,
        source_watermarks,
        status_blocking_records=newly_observed_versions,
        required_data_domains=tuple(
            dict.fromkeys(
                item.domain
                for item in candidate.coverage.domains
                if item.requirement is CoverageRequirement.REQUIRED
            )
        ),
    )
    if any(item.requires_full_analysis for item in change_signals):
        supports_quiet = False
    if question_disposition is not None and question_disposition.status == "limited":
        supports_quiet = False
    coverage_domains = (
        tuple(item for item in candidate.coverage.domains if item.source is None) + source_domains
    )

    confidence_rank = {
        ClaimConfidence.LOW: 0,
        ClaimConfidence.INDETERMINATE: 1,
        ClaimConfidence.MEDIUM: 2,
        ClaimConfidence.HIGH: 3,
    }
    claim_delta: list[ClaimRevisionDelta] = []
    for original in candidate.current_state.claims:
        previous = claim_matches.get(original.id)
        if previous is None:
            claim_delta.append(
                ClaimRevisionDelta(
                    object_id=original.id,
                    change=ClaimChange.INTRODUCED,
                    identity_disposition=(
                        IdentityDisposition.AMBIGUOUS_NEW
                        if original.id in ambiguous_claims
                        else IdentityDisposition.NEW
                    ),
                )
            )
            continue
        current = next(item for item in claims if item.id == previous.id)
        if current.standing is ClaimStanding.INVALIDATED:
            change = ClaimChange.INVALIDATED
        elif confidence_rank[current.confidence] > confidence_rank[previous.confidence]:
            change = ClaimChange.STRENGTHENED
        elif confidence_rank[current.confidence] < confidence_rank[previous.confidence]:
            change = ClaimChange.WEAKENED
        else:
            change = ClaimChange.REAFFIRMED
        claim_delta.append(
            ClaimRevisionDelta(
                object_id=current.id,
                previous_object_id=previous.id,
                change=change,
                identity_disposition=IdentityDisposition.EXACT_MATCH,
            )
        )
    claim_delta.extend(
        ClaimRevisionDelta(
            object_id=claim.id,
            previous_object_id=claim.id,
            change=ClaimChange.RETIRED,
            identity_disposition=IdentityDisposition.CONSERVATIVE_RETIREMENT,
        )
        for claim in retired_claims
    )

    question_delta: list[QuestionRevisionDelta] = []
    if question_disposition is not None and question_disposition.status == "complete":
        disposition_changes = {
            item.value: QuestionChange(item.value) for item in QuestionDispositionKind
        }
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=item.baseline_question_id,
                previous_object_id=item.baseline_question_id,
                change=disposition_changes[item.disposition.value],
                identity_disposition=IdentityDisposition.EXACT_MATCH,
                evidence_refs=item.evidence_refs,
                reason=item.reason,
                successor_object_id=item.successor_question_id,
            )
            for item in question_disposition.dispositions
        )
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=item.id,
                change=QuestionChange.INTRODUCED,
                identity_disposition=IdentityDisposition.NEW,
            )
            for item in candidate.current_state.questions
            if item.id
            not in {
                record.candidate_question_id
                for record in question_disposition.dispositions
                if record.candidate_question_id is not None
            }
        )
    else:
        for original in candidate.current_state.questions:
            previous = question_matches.get(original.id)
            question_delta.append(
                QuestionRevisionDelta(
                    object_id=question_ids.get(original.id, original.id),
                    previous_object_id=previous.id if previous is not None else None,
                    change=(
                        QuestionChange.REAFFIRMED
                        if previous is not None
                        else QuestionChange.INTRODUCED
                    ),
                    identity_disposition=(
                        IdentityDisposition.EXACT_MATCH
                        if previous is not None
                        else (
                            IdentityDisposition.AMBIGUOUS_NEW
                            if original.id in ambiguous_questions
                            else IdentityDisposition.NEW
                        )
                    ),
                )
            )
        question_delta.extend(
            QuestionRevisionDelta(
                object_id=question.id,
                previous_object_id=question.id,
                change=QuestionChange.REAFFIRMED,
                identity_disposition=IdentityDisposition.EXACT_MATCH,
            )
            for question in retired_questions
        )

    coverage_claims = tuple(
        item.model_copy(update={"object_id": claim_ids.get(item.object_id, item.object_id)})
        for item in candidate.coverage.claims
    ) + tuple(
        ResearchObjectCoverage(
            object_id=claim.id,
            status=CoverageStatus.LIMITED,
            evidence_refs=claim.evidence_refs,
            limitations=("The independent Full Analysis did not reproduce this Claim.",),
        )
        for claim in retired_claims
    )
    if question_disposition is not None and question_disposition.status == "complete":
        disposition_by_baseline = {
            item.baseline_question_id: item for item in question_disposition.dispositions
        }
        candidate_coverage_by_id = {item.object_id: item for item in candidate.coverage.questions}
        coverage_questions = tuple(
            ResearchObjectCoverage(
                object_id=question.id,
                status=(
                    CoverageStatus.COMPLETE
                    if question.id in disposition_by_baseline
                    else candidate_coverage_by_id.get(
                        question.id,
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    ).status
                ),
                evidence_refs=(
                    disposition_by_baseline[question.id].evidence_refs
                    if question.id in disposition_by_baseline
                    else candidate_coverage_by_id.get(
                        question.id,
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    ).evidence_refs
                ),
                limitations=(),
            )
            for question in questions
        )
    else:
        coverage_questions = tuple(
            item.model_copy(update={"object_id": question_ids.get(item.object_id, item.object_id)})
            for item in candidate.coverage.questions
        ) + tuple(
            ResearchObjectCoverage(
                object_id=question.id,
                status=CoverageStatus.LIMITED,
                evidence_refs=question.evidence_refs,
                limitations=("Question Disposition was not completed.",),
            )
            for question in retired_questions
        )
    if any(item.status is not CoverageStatus.COMPLETE for item in coverage_questions):
        supports_quiet = False
    opinion_changed = opinion.model_dump(
        exclude={"evidence_refs"}
    ) != baseline.current_state.opinion.model_dump(exclude={"evidence_refs"})

    def semantic_scenarios(values: tuple[ResearchScenarioState, ...]):
        return tuple(item.model_dump(exclude={"cutoff", "evidence_refs"}) for item in values)

    def semantic_factors(values: tuple[ResearchFactor, ...]):
        return tuple(item.model_dump(exclude={"evidence_refs"}) for item in values)

    changed_sections: list[str] = []
    if opinion_changed:
        changed_sections.append("opinion")
    if any(item.change is not ClaimChange.REAFFIRMED for item in claim_delta):
        changed_sections.append("claims")
    question_change_is_material = any(
        item.change is not QuestionChange.REAFFIRMED for item in question_delta
    )
    if question_disposition is not None and question_disposition.status == "limited":
        question_change_is_material = any(
            item.change is QuestionChange.INTRODUCED
            and item.object_id in independently_introduced_question_ids
            for item in question_delta
        )
    if question_change_is_material:
        changed_sections.append("questions")
    if semantic_scenarios(scenarios) != semantic_scenarios(baseline.current_state.scenarios):
        changed_sections.append("scenarios")
    for section, current_values, baseline_values in (
        ("risks", state.risks, baseline.current_state.risks),
        ("catalysts", state.catalysts, baseline.current_state.catalysts),
        (
            "invalidation_conditions",
            state.invalidation_conditions,
            baseline.current_state.invalidation_conditions,
        ),
    ):
        if semantic_factors(current_values) != semantic_factors(baseline_values):
            changed_sections.append(section)
    material = bool(changed_sections)
    material = material or any(
        item.kind is ResearchChangeKind.MARKET_BOUNDARY_CROSSING for item in change_signals
    )
    delta = RevisionDelta(
        opinion_changed=opinion_changed,
        claims=tuple(claim_delta),
        questions=tuple(question_delta),
        changed_sections=tuple(changed_sections),
        inherited_evidence_refs=inherited_refs,
        new_evidence_refs=tuple(item.ref for item in candidate_bundle.items),
        change_signals=change_signals,
        question_disposition=question_disposition,
    )
    change_conclusion = (
        ResearchChangeConclusion.MATERIAL_CHANGE
        if material
        else (
            ResearchChangeConclusion.NO_MATERIAL_CHANGE
            if supports_quiet
            else ResearchChangeConclusion.INDETERMINATE
        )
    )
    summary_values = {
        "baseline": baseline.cutoff.isoformat(),
        "cutoff": candidate.cutoff.isoformat(),
        "count": len(candidate_bundle.items),
        "outcome": change_conclusion.value,
    }
    summaries = {
        "en": (
            "Full Analysis as of {cutoff} was compared with the {baseline} "
            "Eligible Baseline; {count} Evidence items were newly observed. "
            "Outcome: {outcome}."
        ),
        "zh-CN": (
            "截至 {cutoff} 的完整分析已与 {baseline} 合格基线比较；"
            "本次新观察到 {count} 条证据。结果：{outcome}。"
        ),
        "ja": (
            "{cutoff} 時点のフル分析を {baseline} の適格ベースラインと比較し、"
            "{count} 件の新規 Evidence を確認しました。結果: {outcome}。"
        ),
    }
    summary_template = summaries.get(candidate.current_state.language)
    update_summary_text = (
        summary_template.format(**summary_values)
        if summary_template is not None
        else candidate.update_summary.summary
    )
    updated = candidate.model_copy(
        update={
            "execution_strategy": ResearchExecutionStrategy.FULL,
            "role": ResearchRevisionRole.UPDATE,
            "change_conclusion": change_conclusion,
            "indeterminate_reason": (
                (
                    IndeterminateReason.QUESTION_DISPOSITION_LIMITED
                    if question_disposition is not None and question_disposition.status == "limited"
                    else IndeterminateReason.COVERAGE_INCOMPLETE
                )
                if change_conclusion is ResearchChangeConclusion.INDETERMINATE
                else None
            ),
            "delta": delta,
            "current_state": state,
            "coverage": candidate.coverage.model_copy(
                update={
                    "claims": coverage_claims,
                    "questions": coverage_questions,
                    "domains": coverage_domains,
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *candidate.coverage.limitations,
                                *(value for item in source_domains for value in item.limitations),
                                *(
                                    (question_disposition.limitation_reason.value,)
                                    if question_disposition is not None
                                    and question_disposition.limitation_reason is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                    "supports_no_material_change": supports_quiet,
                }
            ),
            "update_summary": candidate.update_summary.model_copy(
                update={
                    "summary": update_summary_text,
                    "baseline_cutoff": baseline.cutoff,
                    "analysis_cutoff": candidate.cutoff,
                    "execution_strategy": ResearchExecutionStrategy.FULL,
                    "change_conclusion": change_conclusion,
                    "new_evidence_refs": tuple(item.ref for item in candidate_bundle.items),
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *candidate.update_summary.limitations,
                                *(value for item in source_domains for value in item.limitations),
                                *(
                                    (question_disposition.limitation_reason.value,)
                                    if question_disposition is not None
                                    and question_disposition.limitation_reason is not None
                                    else ()
                                ),
                            )
                        )
                    ),
                }
            ),
            "evidence_snapshot": EffectiveEvidenceSnapshot(
                bundle=combined_bundle,
                lineage=tuple(
                    EvidenceSnapshotItem(
                        evidence_ref=item.ref,
                        lineage="new" if item.ref in new_refs else "inherited",
                        source_revision_id=(None if item.ref in new_refs else baseline_revision_id),
                    )
                    for item in combined_items
                ),
                source_records=combined_versions,
                source_record_lineage=source_record_lineage,
                source_watermarks=source_watermarks,
            ),
        }
    )
    return ResearchRevisionDraft.model_validate(updated.model_dump(mode="python"))


def assemble_full_revision(
    request: AnalysisRequest,
    execution: FullResearchExecution,
) -> ResearchRevisionDraft:
    """Assemble a complete initial revision after conclusion-independent research."""
    evidence = execution.evidence
    decision = execution.decision
    reports = execution.reports
    evidence_refs = tuple(item.ref for item in evidence.items)
    if not evidence_refs:
        raise ValueError("Research State Assembly requires sealed Evidence")
    if decision is None:
        raise ValueError("Research State Assembly requires a Research Decision")
    allowed_refs = set(evidence_refs)
    decision_refs = tuple(ref for ref in decision.evidence_refs if ref in allowed_refs)
    if not decision_refs:
        raise ValueError("Research Opinion requires explicit Evidence refs")
    language = report_language_value(request.output_language or "en")
    claims: list[ResearchClaim] = []

    for report in reports.values():
        for candidate in report.key_claims:
            refs = tuple(ref for ref in candidate.evidence_refs if ref in allowed_refs)
            if not refs:
                raise ValueError("Research Claims require explicit Evidence refs")
            available_sources = {
                source
                for item in evidence.items
                if item.ref in refs
                for source in (item.source, *(origin.source for origin in item.origins))
            }
            if not set(candidate.required_sources).issubset(available_sources):
                raise ValueError("Research Claim requires a source absent from cited Evidence")
            kind = EpistemicKind(candidate.kind.value)
            observed_dates = [
                item.effective_date or item.requested_date
                for item in evidence.items
                if item.ref in refs
            ]
            claims.append(
                ResearchClaim(
                    id=_new_claim_id(),
                    statement=candidate.statement,
                    epistemic_kind=kind,
                    decision_role=DecisionRole.THESIS,
                    confidence=_claim_confidence(candidate.confidence),
                    evidence_refs=refs,
                    required_sources=candidate.required_sources,
                    observed_at=(
                        max(observed_dates) if kind is EpistemicKind.OBSERVATION else None
                    ),
                    falsifier=(
                        None
                        if kind is EpistemicKind.OBSERVATION
                        else (
                            decision.invalidation_conditions[0]
                            if decision.invalidation_conditions
                            else f"Observable Evidence contradicts: {candidate.statement}"
                        )
                    ),
                )
            )
    if not claims:
        claims.append(
            ResearchClaim(
                id=_new_claim_id(),
                statement=decision.thesis,
                epistemic_kind=EpistemicKind.INFERENCE,
                decision_role=DecisionRole.THESIS,
                confidence=_claim_confidence(decision.confidence),
                evidence_refs=decision_refs,
                evidence_relationship="decision_envelope",
                falsifier=(
                    decision.invalidation_conditions[0]
                    if decision.invalidation_conditions
                    else "Observable Evidence contradicts the thesis."
                ),
            )
        )
    primary_claim_ids = tuple(claim.id for claim in claims)

    def factors(statements: tuple[str, ...], role: DecisionRole) -> tuple[ResearchFactor, ...]:
        output: list[ResearchFactor] = []
        for statement in statements:
            claim = ResearchClaim(
                id=_new_claim_id(),
                statement=statement,
                epistemic_kind=(
                    EpistemicKind.FORECAST
                    if role is DecisionRole.CATALYST
                    else EpistemicKind.INFERENCE
                ),
                decision_role=role,
                confidence=ClaimConfidence.INDETERMINATE,
                evidence_refs=decision_refs,
                evidence_relationship="decision_envelope",
                falsifier=f"Observable Evidence disproves: {statement}",
            )
            claims.append(claim)
            output.append(
                ResearchFactor(
                    statement=statement,
                    claim_ids=(claim.id,),
                    evidence_refs=decision_refs,
                )
            )
        return tuple(output)

    risks = factors(decision.risks, DecisionRole.RISK)
    catalysts = factors(decision.catalysts, DecisionRole.CATALYST)
    invalidations = factors(decision.invalidation_conditions, DecisionRole.INVALIDATION)
    scenarios: list[ResearchScenarioState] = []
    for scenario in decision.scenarios:
        scenario_refs = tuple(ref for ref in scenario.evidence_refs if ref in allowed_refs)
        if not scenario_refs:
            raise ValueError("Research Scenarios require explicit Evidence refs")
        assumption_ids = []
        for assumption in scenario.core_assumptions:
            claim = ResearchClaim(
                id=_new_claim_id(),
                statement=assumption,
                epistemic_kind=EpistemicKind.INFERENCE,
                decision_role=DecisionRole.SCENARIO_ASSUMPTION,
                confidence=ClaimConfidence.INDETERMINATE,
                evidence_refs=scenario_refs,
                falsifier=f"Observable Evidence disproves: {assumption}",
            )
            claims.append(claim)
            assumption_ids.append(claim.id)
        scenarios.append(
            ResearchScenarioState(
                kind=scenario.kind,
                likelihood=ScenarioLikelihood.INDETERMINATE,
                cutoff=request.analysis_date,
                horizon=decision.time_horizon,
                outcome=scenario.outcome,
                assumption_claim_ids=tuple(assumption_ids),
                evidence_refs=scenario_refs,
            )
        )
    question_dependencies = {
        item.question: item.required_sources for item in decision.question_source_dependencies
    }
    questions = tuple(
        ResearchQuestion(
            id=_new_question_id(),
            question=question,
            status=QuestionStatus.OPEN,
            required_sources=question_dependencies.get(question, ()),
        )
        for question in decision.unresolved_questions
    )
    state = CurrentResearchState(
        language=language,
        instrument=request.ticker,
        cutoff=request.analysis_date,
        opinion=ResearchOpinion(
            rating=decision.rating,
            confidence=_decision_confidence(decision.confidence),
            thesis=decision.thesis,
            primary_claim_ids=primary_claim_ids,
            evidence_refs=decision_refs,
        ),
        claims=tuple(claims),
        questions=questions,
        scenarios=tuple(scenarios),
        risks=risks,
        catalysts=catalysts,
        invalidation_conditions=invalidations,
        market_reference_levels=decision.market_reference_levels,
        evidence_refs=evidence_refs,
    )
    source_records, source_watermarks = _source_metadata(evidence)
    source_domains, supports_quiet = _source_coverage(
        state,
        source_records,
        source_watermarks,
        required_data_domains=request.analysts,
    )
    domains: list[ResearchDomainCoverage] = []
    limitations: list[str] = []
    for analyst in request.analysts:
        report = reports.get(analyst)
        complete = bool(
            report is not None
            and getattr(report.audit_status, "value", report.audit_status) == "complete"
        )
        domain_limitations = () if complete else (f"{analyst} audit incomplete",)
        limitations.extend(domain_limitations)
        domains.append(
            ResearchDomainCoverage(
                domain=analyst,
                requirement=(
                    CoverageRequirement.ADVISORY
                    if analyst in {"social", "news"}
                    else CoverageRequirement.REQUIRED
                ),
                status=(CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED),
                evidence_refs=(
                    tuple(ref for ref in report.source_refs if ref in allowed_refs)
                    if report is not None
                    else ()
                ),
                limitations=domain_limitations,
            )
        )
    domains.extend(source_domains)
    for domain in source_domains:
        limitations.extend(domain.limitations)
    complete_domain_refs = {
        ref
        for domain in domains
        if domain.status is CoverageStatus.COMPLETE
        for ref in domain.evidence_refs
    }
    claim_coverage: list[ResearchObjectCoverage] = []
    for claim in claims:
        direct = claim.evidence_relationship == "direct"
        complete = direct and set(claim.evidence_refs).issubset(complete_domain_refs)
        limitation = (
            "Granular Evidence relationship is unavailable; only the final "
            "decision Evidence envelope was recorded."
            if not direct
            else "One or more supporting domains have limited coverage."
        )
        claim_limitations = () if complete else (limitation,)
        limitations.extend(claim_limitations)
        claim_coverage.append(
            ResearchObjectCoverage(
                object_id=claim.id,
                status=(CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED),
                evidence_refs=claim.evidence_refs,
                limitations=claim_limitations,
            )
        )
    question_coverage = tuple(
        ResearchObjectCoverage(
            object_id=question.id,
            status=CoverageStatus.LIMITED,
            limitations=("Question remains open without answering Evidence.",),
        )
        for question in questions
    )
    if question_coverage:
        limitations.append("One or more Research Questions remain open.")
    summaries = {
        "en": "Initial Full Analysis established the first Current Research State.",
        "zh-CN": "首次完整分析已建立第一版当前研究状态。",
        "ja": "初回のフル分析で最初の現在研究状態を確立しました。",
    }
    return ResearchRevisionDraft(
        cutoff=request.analysis_date,
        role=ResearchRevisionRole.INITIAL,
        execution_strategy=ResearchExecutionStrategy.FULL,
        change_conclusion=None,
        delta=RevisionDelta(
            opinion_changed=True,
            claims=tuple(
                ClaimRevisionDelta(
                    object_id=claim.id,
                    change=ClaimChange.INTRODUCED,
                    identity_disposition=IdentityDisposition.NEW,
                )
                for claim in claims
            ),
            questions=tuple(
                QuestionRevisionDelta(
                    object_id=question.id,
                    change=QuestionChange.INTRODUCED,
                    identity_disposition=IdentityDisposition.NEW,
                )
                for question in questions
            ),
            new_evidence_refs=evidence_refs,
        ),
        current_state=state,
        coverage=CoverageAttestation(
            claims=tuple(claim_coverage),
            questions=question_coverage,
            domains=tuple(domains),
            limitations=tuple(dict.fromkeys(limitations)),
            supports_no_material_change=supports_quiet,
        ),
        update_summary=UpdateSummary(
            language=language,
            summary=summaries.get(language, decision.executive_summary),
            checked_domains=tuple(item.domain for item in domains),
            limitations=tuple(dict.fromkeys(limitations)),
            analysis_cutoff=request.analysis_date,
            execution_strategy=ResearchExecutionStrategy.FULL,
            change_conclusion=None,
            new_evidence_refs=evidence_refs,
        ),
        evidence_snapshot=EffectiveEvidenceSnapshot(
            bundle=evidence,
            lineage=tuple(
                EvidenceSnapshotItem(evidence_ref=ref, lineage="new") for ref in evidence_refs
            ),
            source_records=source_records,
            source_record_lineage=tuple(
                SourceRecordSnapshotItem(
                    version_id=record.version_id,
                    lineage="new",
                    observed_in_execution=True,
                )
                for record in source_records
            ),
            source_watermarks=source_watermarks,
        ),
    )
