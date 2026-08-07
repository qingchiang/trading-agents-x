"""Versioned contracts for longitudinal Research Chains and Revisions."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from enum import Enum
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class ResearchExecutionStrategy(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class ResearchRevisionOutcome(str, Enum):
    MATERIAL_CHANGE = "material_change"
    NO_MATERIAL_CHANGE = "no_material_change"


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
        active_ids = {
            claim.id for claim in self.claims if claim.standing is ClaimStanding.ACTIVE
        }
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
            claim_id
            for scenario in self.scenarios
            for claim_id in scenario.assumption_claim_ids
        }
        for factor in (*self.risks, *self.catalysts, *self.invalidation_conditions):
            linked_ids.update(factor.claim_ids)
        if not linked_ids.issubset(active_ids):
            raise ValueError("state relationships must use active Claim IDs")
        linked_refs = set(self.opinion.evidence_refs)
        linked_refs.update(ref for claim in self.claims for ref in claim.evidence_refs)
        linked_refs.update(
            ref for scenario in self.scenarios for ref in scenario.evidence_refs
        )
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


class UpdateSummary(ResearchModel):
    schema_version: Literal["1"] = "1"
    language: str
    summary: str = Field(min_length=1)
    checked_domains: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: ReportLanguage | str) -> str:
        return report_language_value(value)


class EvidenceSnapshotItem(ResearchModel):
    evidence_ref: str = Field(pattern=_EVIDENCE_REF)
    lineage: Literal["new", "inherited"]
    source_revision_id: str | None = None


class EffectiveEvidenceSnapshot(ResearchModel):
    schema_version: Literal["1"] = "1"
    bundle: EvidenceBundle
    lineage: tuple[EvidenceSnapshotItem, ...]

    @model_validator(mode="after")
    def validate_lineage(self) -> EffectiveEvidenceSnapshot:
        refs = {item.ref for item in self.bundle.items}
        if {item.evidence_ref for item in self.lineage} != refs:
            raise ValueError("Evidence lineage must cover the complete bundle")
        if any(
            item.lineage == "inherited" and not item.source_revision_id
            for item in self.lineage
        ):
            raise ValueError("inherited Evidence requires a source Revision")
        return self


class ResearchRevisionDraft(ResearchModel):
    cutoff: date
    execution_strategy: ResearchExecutionStrategy
    outcome: ResearchRevisionOutcome
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
        if question_ids != set(covered_question_ids) or len(
            covered_question_ids
        ) != len(set(covered_question_ids)):
            raise ValueError(
                "Coverage must attest every Research Question exactly once"
            )
        snapshot_refs = {item.ref for item in self.evidence_snapshot.bundle.items}
        if not set(self.current_state.evidence_refs).issubset(snapshot_refs):
            raise ValueError(
                "Current Research State uses Evidence outside its snapshot"
            )
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
        lines.append(
            f"- {domain.domain}: {domain.status.value}; limitations: {limitation}"
        )
    lines.extend(["", "### Claim Coverage", ""])
    for item in revision.coverage.claims:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(
            f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}"
        )
    lines.extend(["", "### Question Coverage", ""])
    for item in revision.coverage.questions:
        limitation = "; ".join(item.limitations) or "none"
        lines.append(
            f"- `{item.object_id}`: {item.status.value}; limitations: {limitation}"
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
                    observed_at=(
                        max(observed_dates)
                        if kind is EpistemicKind.OBSERVATION
                        else None
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

    def factors(
        statements: tuple[str, ...], role: DecisionRole
    ) -> tuple[ResearchFactor, ...]:
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
        scenario_refs = tuple(
            ref for ref in scenario.evidence_refs if ref in allowed_refs
        )
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
    questions = tuple(
        ResearchQuestion(
            id=_new_question_id(),
            question=question,
            status=QuestionStatus.OPEN,
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
                status=(
                    CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED
                ),
                evidence_refs=(
                    tuple(ref for ref in report.source_refs if ref in allowed_refs)
                    if report is not None
                    else ()
                ),
                limitations=domain_limitations,
            )
        )
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
                status=(
                    CoverageStatus.COMPLETE if complete else CoverageStatus.LIMITED
                ),
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
        current_state=state,
        coverage=CoverageAttestation(
            claims=tuple(claim_coverage),
            questions=question_coverage,
            domains=tuple(domains),
            limitations=tuple(dict.fromkeys(limitations)),
        ),
        update_summary=UpdateSummary(
            language=language,
            summary=summaries.get(language, decision.executive_summary),
            checked_domains=tuple(item.domain for item in domains),
            limitations=tuple(dict.fromkeys(limitations)),
        ),
        evidence_snapshot=EffectiveEvidenceSnapshot(
            bundle=evidence,
            lineage=tuple(
                EvidenceSnapshotItem(evidence_ref=ref, lineage="new")
                for ref in evidence_refs
            ),
        ),
    )
