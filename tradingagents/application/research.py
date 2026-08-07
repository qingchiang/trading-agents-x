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
    ReportLanguage,
    ResearchDecision,
    ResearchRating,
    ResearchScenarioKind,
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


class ResearchExecutionStrategy(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ResearchRevisionOutcome(str, Enum):
    MATERIAL_CHANGE = "material_change"
    NO_MATERIAL_CHANGE = "no_material_change"
    COVERAGE_INCOMPLETE = "coverage_incomplete"


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
        linked_refs.update(ref for scenario in self.scenarios for ref in scenario.evidence_refs)
        linked_refs.update(
            ref
            for factor in (*self.risks, *self.catalysts, *self.invalidation_conditions)
            for ref in factor.evidence_refs
        )
        if not linked_refs.issubset(self.evidence_refs):
            raise ValueError("state relationships reference unknown Evidence")
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
    outcome: ResearchRevisionOutcome | None = None
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
    url: str | None = None
    replaces_version_id: str | None = None
    evidence_ref: str = Field(pattern=_EVIDENCE_REF)
    fallback: bool = False

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


class ResearchRevisionDraft(ResearchModel):
    cutoff: date
    execution_strategy: ResearchExecutionStrategy
    outcome: ResearchRevisionOutcome
    delta: RevisionDelta
    current_state: CurrentResearchState
    coverage: CoverageAttestation
    update_summary: UpdateSummary
    evidence_snapshot: EffectiveEvidenceSnapshot

    @model_validator(mode="after")
    def validate_complete_coverage(self) -> ResearchRevisionDraft:
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
        snapshot_refs = {item.ref for item in self.evidence_snapshot.bundle.items}
        if not set(self.current_state.evidence_refs).issubset(snapshot_refs):
            raise ValueError("Current Research State uses Evidence outside its snapshot")
        return self


class ResearchRevision(ResearchRevisionDraft):
    id: str
    chain_id: str
    sequence: int = Field(ge=1)
    predecessor_revision_id: str | None = None
    producing_run_id: str | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    created_at: datetime


class ResearchChain(ResearchModel):
    id: str
    instrument: str
    is_primary: bool
    current_revision_id: str
    current_revision: ResearchRevision | None = None
    revisions: tuple[ResearchRevision, ...] = ()
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
        f"- Execution strategy: {revision.execution_strategy.value}",
        f"- Outcome: {revision.outcome.value}",
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
    lines.extend(
        f"- `{question.id}` [{question.status.value}] {question.question}"
        for question in state.questions
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
        item.version_id: item
        for item in revision.evidence_snapshot.source_record_lineage
    }
    for record in revision.evidence_snapshot.source_records:
        item = source_lineage[record.version_id]
        lines.append(
            f"- `{record.version_id}` ({record.source} `{record.record_id}`): "
            f"{record.status.value}; {item.lineage}; observed now: "
            f"{str(item.observed_in_execution).lower()}; available: "
            f"{record.available_at.isoformat()}; {record.title}"
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
            if existing is not None and existing != record:
                raise ValueError("Source Record Version identity has conflicting observations")
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
                limitations=tuple(
                    dict.fromkeys((*existing.limitations, *watermark.limitations))
                ),
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
        advisory = watermark.source == "Google News" and watermark.source not in explicitly_required
        requirement = (
            CoverageRequirement.ADVISORY if advisory else CoverageRequirement.REQUIRED
        )
        live_only_required = (
            requirement is CoverageRequirement.REQUIRED
            and any(item.temporal_scope != "point_in_time" for item in source_watermarks)
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
        limitations = tuple(
            dict.fromkeys(
                value for item in source_watermarks for value in item.limitations
            )
        ) + (
            ("Required source coverage is not point-in-time.",)
            if live_only_required
            else ()
        ) + (
            ("Source watermark intervals contain an unscanned gap.",)
            if missing_interval
            else ()
        )
        domains.append(
            ResearchDomainCoverage(
                domain=(
                    "media_news" if watermark.source == "Google News" else "company_disclosures"
                ),
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
        record.status is not SourceRecordStatus.PUBLISHED
        and record.source in {"EDINET", "TDnet"}
        for record in (
            records if status_blocking_records is None else status_blocking_records
        )
    ):
        supports_quiet = False
    return tuple(domains), supports_quiet


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
        question.model_copy(update={"status": QuestionStatus.RETIRED})
        for question in baseline.current_state.questions
        if question.id not in retained_question_ids
    )

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
                    "limitations": tuple(
                        dict.fromkeys((*item.limitations, *overlap_limitation))
                    ),
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
        item for version_id, item in candidate_versions.items() if version_id not in baseline_versions
    )
    source_domains, supports_quiet = _source_coverage(
        state,
        combined_versions,
        source_watermarks,
        status_blocking_records=newly_observed_versions,
    )
    coverage_domains = tuple(
        item for item in candidate.coverage.domains if item.source is None
    ) + source_domains

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
    for original in candidate.current_state.questions:
        previous = question_matches.get(original.id)
        if previous is None:
            change = QuestionChange.INTRODUCED
        elif previous.status is original.status:
            change = QuestionChange.REAFFIRMED
        elif original.status is QuestionStatus.ANSWERED:
            change = QuestionChange.ANSWERED
        elif original.status is QuestionStatus.OPEN:
            change = QuestionChange.REOPENED
        elif original.status is QuestionStatus.SUPERSEDED:
            change = QuestionChange.SUPERSEDED
        else:
            change = QuestionChange.RETIRED
        question_delta.append(
            QuestionRevisionDelta(
                object_id=question_ids.get(original.id, original.id),
                previous_object_id=previous.id if previous is not None else None,
                change=change,
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
            change=QuestionChange.RETIRED,
            identity_disposition=IdentityDisposition.CONSERVATIVE_RETIREMENT,
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
    coverage_questions = tuple(
        item.model_copy(update={"object_id": question_ids.get(item.object_id, item.object_id)})
        for item in candidate.coverage.questions
    ) + tuple(
        ResearchObjectCoverage(
            object_id=question.id,
            status=CoverageStatus.LIMITED,
            evidence_refs=question.evidence_refs,
            limitations=("The independent Full Analysis did not reproduce this Question.",),
        )
        for question in retired_questions
    )
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
    if any(item.change is not QuestionChange.REAFFIRMED for item in question_delta):
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
    delta = RevisionDelta(
        opinion_changed=opinion_changed,
        claims=tuple(claim_delta),
        questions=tuple(question_delta),
        changed_sections=tuple(changed_sections),
        inherited_evidence_refs=inherited_refs,
        new_evidence_refs=tuple(item.ref for item in candidate_bundle.items),
    )
    outcome = (
        ResearchRevisionOutcome.MATERIAL_CHANGE
        if material
        else (
            ResearchRevisionOutcome.NO_MATERIAL_CHANGE
            if supports_quiet
            else ResearchRevisionOutcome.COVERAGE_INCOMPLETE
        )
    )
    summary_values = {
        "baseline": baseline.cutoff.isoformat(),
        "cutoff": candidate.cutoff.isoformat(),
        "count": len(candidate_bundle.items),
        "outcome": outcome.value,
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
            "outcome": outcome,
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
                    "outcome": outcome,
                    "new_evidence_refs": tuple(item.ref for item in candidate_bundle.items),
                    "limitations": tuple(
                        dict.fromkeys(
                            (
                                *candidate.update_summary.limitations,
                                *(value for item in source_domains for value in item.limitations),
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
        item.question: item.required_sources
        for item in decision.question_source_dependencies
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
        evidence_refs=evidence_refs,
    )
    source_records, source_watermarks = _source_metadata(evidence)
    source_domains, supports_quiet = _source_coverage(
        state, source_records, source_watermarks
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
        execution_strategy=ResearchExecutionStrategy.FULL,
        outcome=ResearchRevisionOutcome.MATERIAL_CHANGE,
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
            outcome=ResearchRevisionOutcome.MATERIAL_CHANGE,
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
