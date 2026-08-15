"""Versioned contracts for longitudinal Research Chains and Revisions."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import (
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
_NEAR_LIVE_MARKET_REFERENCE_LIMITATION = (
    "Near-live market references remain Research Artifacts and do not become "
    "cutoff-dated Current Research State levels."
)


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FullResearchExecution(Protocol):
    evidence: EvidenceBundle
    decision: ResearchDecision
    reports: dict[str, AnalystReport]


class DecisionConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class ClaimConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class ScenarioLikelihood(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    INDETERMINATE = "indeterminate"


class EpistemicKind(StrEnum):
    OBSERVATION = "observation"
    INFERENCE = "inference"
    FORECAST = "forecast"


class DecisionRole(StrEnum):
    THESIS = "thesis"
    RISK = "risk"
    CATALYST = "catalyst"
    INVALIDATION = "invalidation"
    SCENARIO_ASSUMPTION = "scenario_assumption"


class ClaimStanding(StrEnum):
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    RETIRED = "retired"


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"


class CoverageRequirement(StrEnum):
    REQUIRED = "required"
    ADVISORY = "advisory"


class MarketResearchCapability(StrEnum):
    OFFICIAL_FILING = "official_filing"
    TIMELY_DISCLOSURE = "timely_disclosure"
    FUNDAMENTALS = "fundamentals"
    MARKET_OBSERVATION = "market_observation"
    MEDIA = "media"
    SOCIAL_SENTIMENT = "social_sentiment"
    MACRO = "macro"


class TransitionContinuityRule(StrEnum):
    EVENT_STREAM = "event_stream"
    SNAPSHOT = "snapshot"
    MARKET_SERIES = "market_series"


class CapabilitySourceContract(ResearchModel):
    """Explicit alternatives of complementary configured source sets."""

    capability: MarketResearchCapability
    transition_continuity: TransitionContinuityRule
    acceptable_source_sets: tuple[tuple[str, ...], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> CapabilitySourceContract:
        if any(
            not source_set or any(not source for source in source_set)
            for source_set in self.acceptable_source_sets
        ):
            raise ValueError("capability source sets must contain named configured sources")
        return self


class MarketResearchCapabilityProfile(ResearchModel):
    id: str = Field(min_length=1)
    instrument_suffixes: tuple[str, ...] = ()
    bounded_execution_supported: bool
    minimum_anchor_capabilities: tuple[MarketResearchCapability, ...] = ()
    source_contracts: tuple[CapabilitySourceContract, ...] = ()

    @model_validator(mode="after")
    def validate_contracts(self) -> MarketResearchCapabilityProfile:
        capabilities = tuple(item.capability for item in self.source_contracts)
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("a capability profile must declare each capability once")
        if not set(self.minimum_anchor_capabilities).issubset(capabilities):
            raise ValueError("minimum anchor capabilities require source contracts")
        return self


class AnchorQualificationReason(StrEnum):
    ANCHOR_READINESS_NOT_REQUIRED = "anchor_readiness_not_required"
    LEGACY_ANCHOR_COVERAGE_UNPROVEN = "legacy_anchor_coverage_unproven"
    UNSUPPORTED_MARKET_PROFILE = "unsupported_market_profile"
    NOT_FULL_RESEARCH = "not_full_research"
    INFORMATION_FRONTIER_MISSING = "information_frontier_missing"
    FULL_AUDIT_INCOMPLETE = "full_audit_incomplete"
    CURRENT_STATE_UNUSABLE = "current_state_unusable"
    EVIDENCE_CLOSURE_INVALID = "evidence_closure_invalid"
    POINT_IN_TIME_INVALID = "point_in_time_invalid"
    REQUIRED_CAPABILITY_MISSING = "required_capability_missing"
    REQUIRED_DOMAIN_MISSING = "required_domain_missing"
    INCOMPATIBLE_MARKET_SEMANTICS = "incompatible_market_semantics"


class CapabilityAttestation(ResearchModel):
    capability: MarketResearchCapability
    required: bool = True
    satisfied: bool
    sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class ForwardResearchAnchorQualification(ResearchModel):
    schema_version: Literal["1"] = "1"
    is_forward_research_anchor: bool
    profile_id: str | None = None
    reasons: tuple[AnchorQualificationReason, ...] = ()
    capabilities: tuple[CapabilityAttestation, ...] = ()


class SourceRecordStatus(StrEnum):
    PUBLISHED = "published"
    CORRECTED = "corrected"
    WITHDRAWN = "withdrawn"
    REPLACED = "replaced"


class SourceRecordKind(StrEnum):
    DISCLOSURE = "disclosure"
    FUNDAMENTAL = "fundamental"
    MARKET = "market"


class ResearchChangeKind(StrEnum):
    NEW_FUNDAMENTAL_FILING = "new_fundamental_filing"
    FUNDAMENTAL_CORRECTION = "fundamental_correction"
    FUNDAMENTAL_RESTATEMENT = "fundamental_restatement"
    ACCOUNTING_SCOPE_CHANGE = "accounting_scope_change"
    UNCLASSIFIABLE_FUNDAMENTAL_CHANGE = "unclassifiable_fundamental_change"
    MARKET_SEMANTIC_INCOMPATIBILITY = "market_semantic_incompatibility"
    MARKET_BOUNDARY_CROSSING = "market_boundary_crossing"
    ORDINARY_MARKET_MOVE = "ordinary_market_move"
    UNCHANGED_OBSERVATION = "unchanged_observation"


class ResearchExecutionStrategy(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ResearchRevisionRole(StrEnum):
    INITIAL = "initial"
    UPDATE = "update"


class ResearchChangeConclusion(StrEnum):
    MATERIAL_CHANGE = "material_change"
    NO_MATERIAL_CHANGE = "no_material_change"
    INDETERMINATE = "indeterminate"


class IndeterminateReason(StrEnum):
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    QUESTION_DISPOSITION_LIMITED = "question_disposition_limited"


class NextUpdateReason(StrEnum):
    EXPERIMENT_MODE_OFF = "experiment_mode_off"
    UNSUPPORTED_INCREMENTAL_MARKET = "unsupported_incremental_market"
    LEGACY_ANCHOR_COVERAGE_UNPROVEN = "legacy_anchor_coverage_unproven"
    ANCHOR_COVERAGE_INCOMPLETE = "anchor_coverage_incomplete"
    REQUIRED_SOURCE_COVERAGE_INCOMPLETE = "required_source_coverage_incomplete"
    INDETERMINATE_HEAD = "indeterminate_head"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    INCOMPATIBLE_MARKET_SEMANTICS = "incompatible_market_semantics"
    INVALID_REVISION = "invalid_revision"
    INVALID_SOURCE_DEPENDENCY = "invalid_source_dependency"


class SourceDependencyCompatibilityLimitation(StrEnum):
    """Stable compatibility reason retained without exposing internal IDs."""

    INTERNAL_REFERENCE = "internal_source_reference"


class IncrementalEscalationReason(StrEnum):
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


class SemanticChangeRelationship(StrEnum):
    SUPPORT = "support"
    WEAKENING = "weakening"
    CONTRADICTION = "contradiction"
    ANSWERING = "answering"
    REOPENING = "reopening"
    IRRELEVANCE = "irrelevance"
    UNCERTAINTY = "uncertainty"
    POTENTIALLY_MATERIAL_NOVELTY = "potentially_material_novelty"


class ClaimChange(StrEnum):
    INTRODUCED = "introduced"
    REAFFIRMED = "reaffirmed"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    INVALIDATED = "invalidated"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


class QuestionChange(StrEnum):
    INTRODUCED = "introduced"
    REAFFIRMED = "reaffirmed"
    ANSWERED = "answered"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class QuestionDispositionKind(StrEnum):
    REAFFIRMED = "reaffirmed"
    ANSWERED = "answered"
    REOPENED = "reopened"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class QuestionDispositionLimitation(StrEnum):
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


class IdentityDisposition(StrEnum):
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
    schema_version: Literal["1", "2"] = "1"
    claims: tuple[ResearchObjectCoverage, ...]
    questions: tuple[ResearchObjectCoverage, ...]
    domains: tuple[ResearchDomainCoverage, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()
    supports_no_material_change: bool = True
    anchor_qualification: ForwardResearchAnchorQualification | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


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


class SourceObservationInterval(ResearchModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_interval(self) -> SourceObservationInterval:
        if self.start > self.end:
            raise ValueError("source observation interval start must not follow end")
        return self


class SourceCoverageLimitation(ResearchModel):
    kind: Literal["partial", "unavailable", "archive_truncation", "live_only", "unknown"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"]
    requested_interval: SourceObservationInterval
    observed_intervals: tuple[SourceObservationInterval, ...] = ()
    presentation_text: str = Field(min_length=1)


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
    information_frontier: datetime | None = None
    requested_interval: SourceObservationInterval | None = None
    observed_intervals: tuple[SourceObservationInterval, ...] = ()
    structured_limitations: tuple[SourceCoverageLimitation, ...] = ()

    @model_validator(mode="after")
    def validate_window(self) -> SourceWatermarkSnapshot:
        if self.scanned_start > self.scanned_end:
            raise ValueError("Source Watermark start must not follow end")
        if self.overlap_start is not None and not (
            self.scanned_start <= self.overlap_start <= self.scanned_end
        ):
            raise ValueError("Source Watermark overlap must be inside scanned interval")
        if self.information_frontier is not None and self.information_frontier.utcoffset() is None:
            raise ValueError("Source Information Frontier requires a timezone")
        return self


class TransitionCoverageLimitation(ResearchModel):
    kind: Literal["partial", "unavailable", "archive_truncation", "live_only", "unknown"]
    scope: Literal["pre_anchor", "transition"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"]
    source: str = Field(min_length=1)
    requested_interval: SourceObservationInterval
    observed_intervals: tuple[SourceObservationInterval, ...] = ()
    presentation_text: str = Field(min_length=1)


class TransitionCapabilityAttestation(ResearchModel):
    capability: MarketResearchCapability
    required: bool = True
    complete: bool
    sources: tuple[str, ...] = ()
    checked_intervals: tuple[SourceObservationInterval, ...] = ()
    gaps: tuple[SourceObservationInterval, ...] = ()
    limitations: tuple[TransitionCoverageLimitation, ...] = ()


class TransitionCoverageAttestation(ResearchModel):
    schema_version: Literal["1"] = "1"
    anchor_frontier: datetime
    update_frontier: datetime
    complete: bool
    capabilities: tuple[TransitionCapabilityAttestation, ...] = ()

    @model_validator(mode="after")
    def validate_frontiers(self) -> TransitionCoverageAttestation:
        if self.anchor_frontier.utcoffset() is None or self.update_frontier.utcoffset() is None:
            raise ValueError("Transition Coverage frontiers require timezones")
        if self.update_frontier <= self.anchor_frontier:
            raise ValueError("update frontier must follow anchor frontier")
        return self


class EffectiveEvidenceSnapshot(ResearchModel):
    schema_version: Literal["1", "2"] = "2"
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
    information_frontier: datetime | None = None
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
        if self.information_frontier is not None and self.information_frontier.utcoffset() is None:
            raise ValueError("Information Frontier requires a timezone")
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
    transition_coverage: TransitionCoverageAttestation | None = None
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


class ResearchRevision(ResearchRevisionDraft):
    id: str
    chain_id: str
    sequence: int = Field(ge=1)
    predecessor_revision_id: str | None = None
    producing_run_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime


def legacy_forward_research_anchor_qualification() -> ForwardResearchAnchorQualification:
    return ForwardResearchAnchorQualification(
        is_forward_research_anchor=False,
        reasons=(AnchorQualificationReason.LEGACY_ANCHOR_COVERAGE_UNPROVEN,),
    )


class ResearchChain(ResearchModel):
    id: str
    instrument: str
    is_primary: bool
    current_revision_id: str
    current_revision: ResearchRevision | None = None
    revisions: tuple[ResearchRevision, ...] = ()
    forward_research_anchor: ForwardResearchAnchorQualification = Field(
        default_factory=legacy_forward_research_anchor_qualification,
        description=(
            "Content-derived qualification of the current Revision for future bounded "
            "comparison, independent of its Change Conclusion."
        ),
    )
    next_update_policy: Literal["incremental_allowed", "full_required"] = Field(
        default="full_required",
        description=(
            "Server-derived policy: bounded Incremental Execution starts only from a "
            "qualifying Forward Research Anchor for a supported Japanese Instrument in "
            "an enabled experiment mode; otherwise the current head requires Full Analysis."
        ),
    )
    next_update_reason: NextUpdateReason | None = Field(
        default=None,
        description=(
            "Stable reason that the next manual update requires Full Analysis instead of "
            "bounded Incremental Execution."
        ),
    )
    created_at: datetime
    updated_at: datetime


class RevisionExport(ResearchModel):
    schema_version: Literal["1"] = "1"
    chain: ResearchChain
    revision: ResearchRevision
    linked_reports: dict[str, str] = Field(default_factory=dict)


def render_revision_export_markdown(export: RevisionExport) -> str:
    """Render a readable Revision export through the dedicated export subsystem."""
    from ._exports.revision import render_revision_export_markdown as render

    return render(export)


def render_revision_export_package(export: RevisionExport) -> bytes:
    """Render a Revision ZIP package through the dedicated export subsystem."""
    from ._exports.revision import render_revision_export_package as render

    return render(export)
