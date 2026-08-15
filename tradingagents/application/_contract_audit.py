"""Durable Research Update audit and execution-metrics contracts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from ._contract_base import FrozenModel
from ._contract_evidence import EvidenceBundle


class NodeMetrics(FrozenModel):
    """Resource usage attributed to one research graph node."""

    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)


class RunMetrics(FrozenModel):
    llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_hit_input_tokens: int = Field(default=0, ge=0)
    cache_miss_input_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    detailed_usage_calls: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    node_metrics: dict[str, NodeMetrics] = Field(default_factory=dict)


class ResearchUpdateObjectCoverage(FrozenModel):
    object_id: str
    status: Literal["complete", "limited", "unavailable"]
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class ResearchUpdateDomainCoverage(FrozenModel):
    domain: str
    status: Literal["complete", "limited", "unavailable"]
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    requirement: Literal["required", "advisory"] = "required"
    source: str | None = None


class ResearchUpdateCoverageAttestation(FrozenModel):
    schema_version: Literal["1"] = "1"
    claims: tuple[ResearchUpdateObjectCoverage, ...]
    questions: tuple[ResearchUpdateObjectCoverage, ...]
    domains: tuple[ResearchUpdateDomainCoverage, ...]
    limitations: tuple[str, ...] = ()
    supports_no_material_change: bool


class ResearchUpdateSummaryContract(FrozenModel):
    schema_version: Literal["1"] = "1"
    language: str
    summary: str
    checked_domains: tuple[str, ...]
    limitations: tuple[str, ...] = ()
    baseline_cutoff: date | None = None
    analysis_cutoff: date | None = None
    execution_strategy: Literal["full", "incremental"] | None = None
    change_conclusion: Literal["material_change", "no_material_change", "indeterminate"] | None = (
        None
    )
    new_evidence_refs: tuple[str, ...] = ()


class ResearchUpdateEvidenceSnapshotItem(FrozenModel):
    evidence_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    lineage: Literal["new", "inherited"]
    source_revision_id: str | None = None


class ResearchUpdateSourceRecordVersion(FrozenModel):
    source: str
    record_id: str
    version_id: str
    status: Literal["published", "corrected", "withdrawn", "replaced"]
    published_at: str
    available_at: datetime
    title: str
    availability_basis: str | None = None
    url: str | None = None
    replaces_version_id: str | None = None
    evidence_ref: str = Field(pattern=r"^ev_[a-f0-9]{12}$")
    fallback: bool = False
    record_kind: Literal["disclosure", "fundamental", "market"] = "disclosure"
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


class ResearchUpdateSourceRecordSnapshotItem(FrozenModel):
    version_id: str
    lineage: Literal["new", "inherited"]
    observed_in_execution: bool
    source_revision_id: str | None = None


class ResearchUpdateSourceObservationInterval(FrozenModel):
    start: date
    end: date


class ResearchUpdateSourceCoverageLimitation(FrozenModel):
    kind: Literal["partial", "unavailable", "archive_truncation", "live_only", "unknown"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"]
    requested_interval: ResearchUpdateSourceObservationInterval
    observed_intervals: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    presentation_text: str


class ResearchUpdateSourceWatermarkSnapshot(FrozenModel):
    source: str
    scanned_start: date
    scanned_end: date
    status: Literal["complete", "limited", "unavailable"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"] = "point_in_time"
    limitations: tuple[str, ...] = ()
    returned_records: int = Field(default=0, ge=0)
    reported_records: int | None = Field(default=None, ge=0)
    baseline_cutoff: date | None = None
    overlap_start: date | None = None
    information_frontier: datetime | None = None
    requested_interval: ResearchUpdateSourceObservationInterval | None = None
    observed_intervals: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    structured_limitations: tuple[ResearchUpdateSourceCoverageLimitation, ...] = ()


class ResearchUpdateEvidenceSnapshot(FrozenModel):
    schema_version: Literal["1", "2"] = "2"
    bundle: EvidenceBundle
    lineage: tuple[ResearchUpdateEvidenceSnapshotItem, ...]
    source_records: tuple[ResearchUpdateSourceRecordVersion, ...] = ()
    source_record_lineage: tuple[ResearchUpdateSourceRecordSnapshotItem, ...] = ()
    source_watermarks: tuple[ResearchUpdateSourceWatermarkSnapshot, ...] = ()

    @model_validator(mode="after")
    def validate_closure(self) -> ResearchUpdateEvidenceSnapshot:
        evidence_refs = {item.ref for item in self.bundle.items}
        if {item.evidence_ref for item in self.lineage} != evidence_refs:
            raise ValueError("bounded Evidence lineage must cover its bundle")
        if not {item.evidence_ref for item in self.source_records}.issubset(evidence_refs):
            raise ValueError("bounded Source Records must reference Evidence in their bundle")
        version_ids = {item.version_id for item in self.source_records}
        if {item.version_id for item in self.source_record_lineage} != version_ids:
            raise ValueError("bounded Source Record lineage must cover its versions")
        if not {
            item.replaces_version_id
            for item in self.source_records
            if item.replaces_version_id is not None
        }.issubset(version_ids):
            raise ValueError("bounded Source Record predecessor must resolve in its snapshot")
        return self


class ResearchUpdateCandidate(FrozenModel):
    """Retained bounded-update proposal used by the Shadow experiment."""

    schema_version: Literal["2"] = "2"
    change_conclusion: Literal["no_material_change"]
    coverage: ResearchUpdateCoverageAttestation
    update_summary: ResearchUpdateSummaryContract
    evidence_snapshot: ResearchUpdateEvidenceSnapshot


ResearchUpdateEscalationReason = Literal[
    "invalid_baseline",
    "source_correction",
    "source_withdrawal",
    "source_replacement",
    "source_version_change",
    "incompatible_semantics",
    "threshold_crossing",
    "coverage_incomplete",
    "schema_invalid",
    "semantic_weakening",
    "semantic_contradiction",
    "semantic_answering",
    "semantic_reopening",
    "semantic_uncertainty",
    "potentially_material_novelty",
    "confidence_change",
    "ambiguous_identity",
    "semantic_output_invalid",
    "semantic_input_oversize",
]


class ResearchUpdateSemanticRelationship(FrozenModel):
    evidence_refs: tuple[str, ...]
    relationship: Literal[
        "support",
        "weakening",
        "contradiction",
        "answering",
        "reopening",
        "irrelevance",
        "uncertainty",
        "potentially_material_novelty",
    ]
    suggested_claim_ids: tuple[str, ...] = ()
    suggested_question_ids: tuple[str, ...] = ()
    suggested_claim_confidence: Literal["low", "medium", "high", "indeterminate"] | None = None


class ResearchUpdateSemanticAssessment(FrozenModel):
    schema_version: Literal["1"] = "1"
    language: str
    summary: str
    relationships: tuple[ResearchUpdateSemanticRelationship, ...]


class ResearchUpdateCheckedWindow(FrozenModel):
    source: str
    scanned_start: date
    scanned_end: date
    status: Literal["complete", "limited", "unavailable"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"] = "point_in_time"
    limitations: tuple[str, ...] = ()
    returned_records: int = Field(default=0, ge=0)
    reported_records: int | None = Field(default=None, ge=0)
    baseline_cutoff: date | None = None
    overlap_start: date | None = None
    information_frontier: datetime | None = None
    requested_interval: ResearchUpdateSourceObservationInterval | None = None
    observed_intervals: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    structured_limitations: tuple[ResearchUpdateSourceCoverageLimitation, ...] = ()


class ResearchUpdateTransitionLimitation(FrozenModel):
    kind: Literal["partial", "unavailable", "archive_truncation", "live_only", "unknown"]
    scope: Literal["pre_anchor", "transition"]
    temporal_scope: Literal["point_in_time", "live_only", "unknown"]
    source: str
    requested_interval: ResearchUpdateSourceObservationInterval
    observed_intervals: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    presentation_text: str


class ResearchUpdateTransitionCapability(FrozenModel):
    capability: Literal[
        "official_filing",
        "timely_disclosure",
        "fundamentals",
        "market_observation",
        "media",
        "social_sentiment",
        "macro",
    ]
    required: bool = True
    complete: bool
    sources: tuple[str, ...] = ()
    checked_intervals: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    gaps: tuple[ResearchUpdateSourceObservationInterval, ...] = ()
    limitations: tuple[ResearchUpdateTransitionLimitation, ...] = ()


class ResearchUpdateTransitionCoverage(FrozenModel):
    schema_version: Literal["1"] = "1"
    anchor_frontier: datetime
    update_frontier: datetime
    complete: bool
    capabilities: tuple[ResearchUpdateTransitionCapability, ...] = ()


class ResearchUpdateEvidenceLineage(FrozenModel):
    evidence_ref: str
    lineage: Literal["new", "inherited"]
    source_revision_id: str | None = None


class ResearchUpdateAudit(FrozenModel):
    """Durable phase attribution and finding for one Research Chain update."""

    schema_version: Literal["2", "3"] = "3"
    mode: Literal["shadow", "experimental"] = "shadow"
    candidate: ResearchUpdateCandidate | None = None
    coverage: ResearchUpdateCoverageAttestation | None = None
    checked_windows: tuple[ResearchUpdateCheckedWindow, ...] = ()
    transition_coverage: ResearchUpdateTransitionCoverage | None = None
    evidence_lineage: tuple[ResearchUpdateEvidenceLineage, ...] = ()
    semantic_assessment: ResearchUpdateSemanticAssessment | None = None
    baseline_information_frontier: datetime | None = None
    authoritative_strategy: Literal["full", "incremental"] = "full"
    escalation_reason: ResearchUpdateEscalationReason | None = None
    comparison: Literal["agreement", "disagreement", "inconclusive", "not_applicable"]
    bounded_metrics: RunMetrics = Field(default_factory=RunMetrics)
    full_metrics: RunMetrics = Field(default_factory=RunMetrics)
