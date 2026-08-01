"""Readable research deliberation with shallow routing contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    IssueDisposition,
    JudgeDraft,
    MarketReferenceBasis,
    MarketReferenceLevel,
    MemoryContext,
    NumericAuditAppendixStatus,
    NumericAuditOmission,
    NumericAuditPhase,
    NumericAuditSnapshot,
    NumericAuditStatus,
    RebuttalReview,
    ResearchCase,
    ResearchDecision,
    ResearchRating,
    ResearchScenario,
    ResearchScenarioKind,
    ResearchWarning,
    RiskReview,
    RiskReviewAdjustment,
    RiskReviewDisposition,
    ValuationAssessment,
    ValuationRange,
)
from tradingagents.application.markdown_evidence import normalize_evidence_markdown
from tradingagents.graph.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputFailure,
    StructuredOutputResult,
    StructuredOutputRunner,
)

EventWriter = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ResearchMarkdown:
    """One readable artifact body and non-fatal citation warnings."""

    markdown: str
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


class CalculationRecordDraft(BaseModel):
    """Serializer-facing calculation without dynamic JSON object keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^calc_[a-z0-9][a-z0-9_.-]*$")
    formula: str = Field(min_length=1)
    inputs: tuple[CalculationInputDraft, ...] = Field(min_length=1)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    unit: str = Field(min_length=1, max_length=32)
    limitations: tuple[str, ...] = Field(min_length=1)

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
    confidence: float = Field(ge=0.0, le=1.0)
    executive_summary: str = Field(min_length=1)
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    memory_refs: tuple[str, ...] = ()
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


class ScenarioValuationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ResearchScenarioKind
    valuation_range: ValuationRange
    calculation_ids: tuple[str, ...] = Field(min_length=1)


class ValuationAssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: str = Field(min_length=1)
    valuation_range: ValuationRange
    currency: str = Field(min_length=1, max_length=16)
    input_evidence_refs: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)
    calculation_ids: tuple[str, ...] = Field(min_length=1)


class MarketReferenceLevelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=120)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    interpretation: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    basis: MarketReferenceBasis
    calculation_ids: tuple[str, ...] = ()


class DecisionNumericDraft(BaseModel):
    """Optional valuation and market-reference payload for a decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: bool
    scenario_valuations: tuple[ScenarioValuationDraft, ...] = ()
    valuation_assessment: ValuationAssessmentDraft | None = None
    market_reference_levels: tuple[MarketReferenceLevelDraft, ...] = ()
    calculation_records: tuple[CalculationRecordDraft, ...] = ()


@dataclass(frozen=True)
class ResearchDecisionOutput:
    value: ResearchDecision
    generation_method: ArtifactGenerationMethod
    warnings: tuple[ResearchWarning, ...] = ()
    numeric_audit: DecisionNumericAuditAppendix | None = None


def write_research_markdown(
    llm: Any,
    *,
    prompt: str,
    node: str,
    allowed_evidence_refs: tuple[str, ...],
    invoke_config: dict[str, Any] | None = None,
) -> ResearchMarkdown:
    """Generate one readable deliberation document without a JSON contract."""

    response = llm.invoke(
        prompt
        + "\n\nWrite the complete research reasoning as readable Markdown. "
        "Use headings, concise tables, and evidence footnotes where they help "
        "the reader. Use only inline `[^ev_xxxxxxxxxxxx]` references and never "
        "write footnote definitions; Evidence Ledger supplies source details. "
        "Do not emit JSON, schema fields, or hidden chain-of-thought.",
        config=invoke_config,
    )
    markdown = _message_text(response).strip()
    if not markdown:
        raise StructuredOutputError(
            node=node,
            schema="ResearchMarkdown",
            reason_code="empty_output",
        )
    if _is_truncated(response):
        continuation = llm.invoke(
            (
                "Continue the prior Markdown from its last complete block. "
                "Do not repeat prior content and finish the document."
            ),
            config=invoke_config,
        )
        if _is_truncated(continuation):
            raise StructuredOutputError(
                node=node,
                schema="ResearchMarkdown",
                reason_code="truncated_output",
            )
        continued = _message_text(continuation).strip()
        if continued:
            markdown = f"{markdown.rstrip()}\n\n{continued}"
    normalized = normalize_evidence_markdown(
        markdown,
        allowed_refs=set(allowed_evidence_refs),
        source=node,
    )
    return ResearchMarkdown(
        markdown=normalized.markdown,
        warnings=normalized.warnings,
    )


def invoke_research_case(
    llm: Any,
    *,
    role: str,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[ResearchCase]:
    del llm, state, node, event_writer
    return StructuredOutputResult(
        value=ResearchCase(
            role=role,
            markdown=markdown,
        ),
        generation_method=ArtifactGenerationMethod.MARKDOWN_AUDITED,
    )


def invoke_debate_agenda(
    llm: Any,
    *,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[DebateAgenda]:
    def validate(result: DebateAgenda) -> DebateAgenda:
        require_text(result.summary)
        for issue in result.issues:
            require_text(issue.question)
        return result

    try:
        return _runner(
            llm,
            DebateAgenda,
            validate,
            node,
            event_writer,
        ).invoke(
            prompt
            + "\n\nReturn only a concise agenda summary and distinct material "
            "questions. The full bull and bear reasoning remains in their Markdown.",
            example=DebateAgenda(
                summary="The cases disagree on one material mechanism.",
                issues=(
                    DebateIssue(
                        id="debate.issue_1",
                        question="Will the disputed operating mechanism persist?",
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            ).model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        return StructuredOutputResult(
            value=DebateAgenda(
                summary=(
                    "The completed bull and bear cases contain a material "
                    "disagreement whose navigation audit is incomplete."
                ),
                issues=(
                    DebateIssue(
                        id="debate.issue_audit_fallback",
                        question=(
                            "Which material disagreement between the completed "
                            "bull and bear cases remains unresolved?"
                        ),
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            ),
            generation_method=(
                ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
            ),
        )


def invoke_rebuttal(
    llm: Any,
    *,
    role: str,
    round_number: int,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    conservative_open: bool = False,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[RebuttalReview]:
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    valid_issues = {issue.id for issue in agenda.issues}

    def validate(result: RebuttalAudit) -> RebuttalAudit:
        addressed = set(result.addressed_issue_ids)
        opened = set(result.open_issue_ids)
        if not addressed.issubset(valid_issues) or not opened.issubset(
            valid_issues
        ):
            raise OutputValidationError("navigation.issue.unknown")
        if not addressed:
            raise OutputValidationError("navigation.issue.missing_addressed")
        return result

    first_issue = agenda.issues[0].id
    valid_issue_list = tuple(issue.id for issue in agenda.issues)
    try:
        audited = _runner(
            llm,
            RebuttalAudit,
            validate,
            node,
            event_writer,
        ).invoke(
            (
                "Extract only addressed and still-open DebateAgenda issue IDs "
                "from this completed rebuttal. Do not rewrite the Markdown.\n\n"
                f"VALID ISSUE IDS:\n{json.dumps(valid_issue_list)}\n\n"
                f"MARKDOWN:\n{markdown}"
            ),
            example=RebuttalAudit(
                addressed_issue_ids=(first_issue,),
                open_issue_ids=(first_issue,),
            ).model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        mentioned = _mentioned_ids(markdown, valid_issues)
        addressed = mentioned or valid_issue_list
        return StructuredOutputResult(
            value=RebuttalReview(
                role=role,
                round=round_number,
                markdown=markdown,
                addressed_issue_ids=addressed,
                open_issue_ids=valid_issue_list if conservative_open else (),
            ),
            generation_method=(
                ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
            ),
        )
    return StructuredOutputResult(
        value=RebuttalReview(
            role=role,
            round=round_number,
            markdown=markdown,
            addressed_issue_ids=audited.value.addressed_issue_ids,
            open_issue_ids=audited.value.open_issue_ids,
        ),
        generation_method=audited.generation_method,
    )


def invoke_judge_draft(
    llm: Any,
    *,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    memory: MemoryContext | None = None,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[JudgeDraft]:
    del memory
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    issue_ids = {issue.id for issue in agenda.issues}

    def validate(result: JudgeAudit) -> JudgeAudit:
        actual = {item.issue_id for item in result.issue_dispositions}
        if actual != issue_ids:
            raise OutputValidationError(
                "navigation.issue.disposition_incomplete"
            )
        return result

    example_dispositions = tuple(
        IssueDisposition(issue_id=issue.id, status="unresolved")
        for issue in agenda.issues
    )
    valid_issue_list = tuple(issue.id for issue in agenda.issues)
    try:
        audited = _runner(
            llm,
            JudgeAudit,
            validate,
            node,
            event_writer,
        ).invoke(
            (
                "Extract the preliminary rating, calibrated confidence, and one "
                "routing disposition for every agenda issue from this completed "
                "judge Markdown. Do not rewrite the Markdown.\n\n"
                f"VALID ISSUE IDS:\n{json.dumps(valid_issue_list)}\n\n"
                f"MARKDOWN:\n{markdown}"
            ),
            example=JudgeAudit(
                preliminary_rating=ResearchRating.HOLD,
                confidence=0.55,
                issue_dispositions=example_dispositions,
            ).model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        return StructuredOutputResult(
            value=JudgeDraft(
                markdown=markdown,
                preliminary_rating=None,
                confidence=None,
                issue_dispositions=example_dispositions,
            ),
            generation_method=(
                ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
            ),
        )
    return StructuredOutputResult(
        value=JudgeDraft(
            markdown=markdown,
            preliminary_rating=audited.value.preliminary_rating,
            confidence=audited.value.confidence,
            issue_dispositions=audited.value.issue_dispositions,
        ),
        generation_method=audited.generation_method,
    )


def invoke_risk_review(
    llm: Any,
    *,
    role: str,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[RiskReview]:
    del llm, node, event_writer
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    valid_issues = {issue.id for issue in agenda.issues}
    challenged = _mentioned_ids(markdown, valid_issues)
    unresolved = _mentioned_ids(
        "\n".join(
            line
            for line in markdown.splitlines()
            if re.search(
                r"\b(?:unresolved|open|uncertain)\b|未解决|尚未|不确定",
                line,
                flags=re.IGNORECASE,
            )
        ),
        valid_issues,
    )
    return StructuredOutputResult(
        value=RiskReview(
            role=role,
            markdown=markdown,
            challenged_issue_ids=challenged,
            unresolved_issue_ids=unresolved,
        ),
        generation_method=ArtifactGenerationMethod.MARKDOWN_AUDITED,
    )


def invoke_research_decision(
    llm: Any,
    *,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    memory: MemoryContext | None = None,
    require_risk_adjustments: bool,
    event_writer: EventWriter | None = None,
) -> ResearchDecisionOutput:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    valid_refs = tuple(item.ref for item in bundle.items)
    valid_memory_refs = tuple(memory.refs if memory is not None else ())
    first_ref = valid_refs[0]
    risk_roles = tuple(state.get("risk_reviews", {}))
    example_adjustments = (
        (
            RiskReviewAdjustment(
                source_role=risk_roles[0],
                disposition=RiskReviewDisposition.MODIFIED,
                subject="Confidence calibration",
                explanation="The final decision incorporates the risk review.",
                evidence_refs=(first_ref,),
            ),
        )
        if risk_roles
        else ()
    )

    def validate_core(
        result: ResearchDecisionCoreDraft,
    ) -> ResearchDecisionCoreDraft:
        scenario_kinds = tuple(item.kind for item in result.scenarios)
        if len(set(scenario_kinds)) != len(scenario_kinds):
            raise OutputValidationError("decision.scenarios.duplicate_kind")
        if set(scenario_kinds) != set(ResearchScenarioKind):
            raise OutputValidationError("decision.scenarios.incomplete_set")
        require_text(result.executive_summary)
        require_text(result.thesis)
        require_nonempty_texts(result.risks)
        require_nonempty_texts(result.invalidation_conditions)
        require_text(result.time_horizon)
        require_valid_refs(result.evidence_refs, set(valid_refs), required=True)
        require_valid_refs(
            result.memory_refs,
            set(valid_memory_refs),
            required=False,
        )
        for scenario in result.scenarios:
            require_nonempty_texts(scenario.core_assumptions)
            require_text(scenario.outcome)
            require_valid_refs(
                scenario.evidence_refs,
                set(valid_refs),
                required=True,
            )
        if require_risk_adjustments:
            adjusted_roles = {
                item.source_role for item in result.risk_review_adjustments
            }
            if not set(risk_roles).issubset(adjusted_roles):
                raise OutputValidationError(
                    "decision.risk_review.missing_role"
                )
        if any(
            item.source_role not in risk_roles
            for item in result.risk_review_adjustments
        ):
            raise OutputValidationError(
                "decision.risk_review.unknown_role"
            )
        return result

    core = StructuredOutputRunner(
        llm=llm,
        schema=ResearchDecisionCoreDraft,
        validator=validate_core,
        node=f"{node}.core",
        event_writer=event_writer,
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
        invoke_config={"metadata": {"research_node": f"{node}.core"}},
        repair_instructions=(
            "Keep valid research content. Use only allowed evidence and memory "
            "refs. Do not include valuation ranges, market-reference levels, "
            "or calculations in this core object. The scenarios must contain "
            "exactly one base, one bull, and one bear case. Required "
            f"risk-review roles: {json.dumps(risk_roles)}."
        ),
    ).invoke(
        prompt
        + "\n\nSerialize only the strict qualitative decision core. Numeric "
        "valuation, scenario ranges, market reference levels, and calculations "
        "are handled by a separate audit step.",
        example=ResearchDecisionCoreDraft(
            rating=ResearchRating.HOLD,
            confidence=0.5,
            executive_summary="The evidence supports a balanced conclusion.",
            thesis="The view depends on a testable operating mechanism.",
            evidence_refs=(first_ref,),
            risks=("The evidence-backed downside may materialize.",),
            invalidation_conditions=(
                "New evidence directly contradicts the thesis.",
            ),
            unresolved_questions=("Which scenario will dominate?",),
            time_horizon="6-12 months",
            scenarios=(
                    ResearchScenarioCoreDraft(
                    kind=ResearchScenarioKind.BASE,
                    core_assumptions=(
                        "Current evidence remains representative.",
                    ),
                    outcome="The thesis develops broadly as expected.",
                    evidence_refs=(first_ref,),
                ),
                    ResearchScenarioCoreDraft(
                    kind=ResearchScenarioKind.BULL,
                    core_assumptions=(
                        "The constructive mechanism strengthens.",
                    ),
                    outcome="The result exceeds the base case.",
                    evidence_refs=(first_ref,),
                ),
                    ResearchScenarioCoreDraft(
                    kind=ResearchScenarioKind.BEAR,
                    core_assumptions=("The principal risk materializes.",),
                    outcome="The result falls below the base case.",
                    evidence_refs=(first_ref,),
                ),
            ),
            risk_review_adjustments=example_adjustments,
        ).model_dump(mode="json"),
        allowed_evidence_refs=valid_refs,
        allowed_memory_refs=valid_memory_refs,
    )

    numeric = _invoke_decision_numeric(
        llm,
        prompt=prompt,
        node=f"{node}.numeric",
        bundle=bundle,
        allowed_evidence_refs=valid_refs,
        event_writer=event_writer,
    )
    core_value = core.value
    scenario_values = []
    for scenario in core_value.scenarios:
        numeric_scenario = numeric.scenario_valuations.get(scenario.kind)
        scenario_values.append(
            ResearchScenario(
                kind=scenario.kind,
                core_assumptions=scenario.core_assumptions,
                outcome=scenario.outcome,
                evidence_refs=scenario.evidence_refs,
                valuation_range=(
                    numeric_scenario.valuation_range
                    if numeric_scenario is not None
                    else None
                ),
                valuation_calculation_ids=(
                    numeric_scenario.calculation_ids
                    if numeric_scenario is not None
                    else ()
                ),
            )
        )
    decision = ResearchDecision(
        rating=core_value.rating,
        confidence=core_value.confidence,
        executive_summary=core_value.executive_summary,
        thesis=core_value.thesis,
        evidence_refs=core_value.evidence_refs,
        memory_refs=core_value.memory_refs,
        catalysts=core_value.catalysts,
        risks=core_value.risks,
        invalidation_conditions=core_value.invalidation_conditions,
        unresolved_questions=core_value.unresolved_questions,
        time_horizon=core_value.time_horizon,
        scenarios=tuple(scenario_values),
        valuation_assessment=numeric.valuation_assessment,
        market_reference_levels=numeric.market_reference_levels,
        calculation_records=numeric.calculation_records,
        risk_review_adjustments=core_value.risk_review_adjustments,
        numeric_audit_status=numeric.status,
    )
    return ResearchDecisionOutput(
        value=decision,
        generation_method=core.generation_method,
        warnings=numeric.warnings,
        numeric_audit=numeric.audit,
    )


@dataclass(frozen=True)
class _NumericDecisionAssembly:
    scenario_valuations: dict[ResearchScenarioKind, ScenarioValuationDraft]
    valuation_assessment: ValuationAssessment | None
    market_reference_levels: tuple[MarketReferenceLevel, ...]
    calculation_records: tuple[CalculationRecord, ...]
    status: NumericAuditStatus
    warnings: tuple[ResearchWarning, ...] = ()
    issues: tuple[str, ...] = ()
    omissions: tuple[NumericAuditOmission, ...] = ()
    audit: DecisionNumericAuditAppendix | None = None


def _invoke_decision_numeric(
    llm: Any,
    *,
    prompt: str,
    node: str,
    bundle: EvidenceBundle,
    allowed_evidence_refs: tuple[str, ...],
    event_writer: EventWriter | None,
) -> _NumericDecisionAssembly:
    allowed = set(allowed_evidence_refs)

    def validate(draft: DecisionNumericDraft) -> DecisionNumericDraft:
        _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            salvage=False,
            node=node,
        )
        return draft

    def numeric_event(raw: dict[str, Any]) -> None:
        if event_writer is None:
            return
        mapped = {
            "node.output_retry": "node.numeric_audit_retry",
            "node.output_recovered": "node.numeric_audit_recovered",
            "node.output_failed": "node.numeric_audit_degraded",
        }.get(raw.get("event_type"), raw.get("event_type"))
        event_writer({**raw, "event_type": mapped})

    example = DecisionNumericDraft(
        requested=True,
        valuation_assessment=ValuationAssessmentDraft(
            method="Evidence-backed earnings multiple",
            valuation_range=ValuationRange(low=90, high=110),
            currency="USD",
            input_evidence_refs=(allowed_evidence_refs[0],),
            limitations=("The multiple is scenario-dependent.",),
            calculation_ids=("calc_valuation",),
        ),
        market_reference_levels=(
            MarketReferenceLevelDraft(
                label="Observed recent close",
                value=100,
                unit="USD",
                interpretation=(
                    "A directly observed reference, not an execution order."
                ),
                evidence_refs=(allowed_evidence_refs[0],),
                basis=MarketReferenceBasis.OBSERVED,
            ),
        ),
        calculation_records=(
            CalculationRecordDraft(
                id="calc_valuation",
                formula="earnings * multiple",
                inputs=(
                    CalculationInputDraft(name="earnings", value=10),
                    CalculationInputDraft(name="multiple", value=10),
                ),
                input_evidence_refs=(allowed_evidence_refs[0],),
                unit="USD",
                limitations=("The multiple is scenario-dependent.",),
            ),
        ),
    )
    runner = StructuredOutputRunner(
        llm=llm,
        schema=DecisionNumericDraft,
        validator=validate,
        node=node,
        event_writer=numeric_event,
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
        invoke_config={"metadata": {"research_node": node}},
        repair_instructions=(
            "Repair only the optional numeric appendix. Calculation input "
            "names must be ASCII identifiers and the formula must use every "
            "input exactly. Observed market references require evidence but "
            "no calculation. Derived references, valuation assessments, and "
            "scenario valuation ranges must name valid calculation IDs. Do not "
            "supply calculation results or dates; the application derives both "
            "from the formula and Evidence Ledger. Do not change the qualitative "
            "decision core."
        ),
    )
    try:
        output = runner.invoke(
            prompt
            + "\n\nExtract only optional decision-critical numeric content. "
            "Set requested=false and return empty collections when the brief "
            "does not support a numeric appendix. Do not copy ordinary report "
            "table arithmetic.",
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=allowed_evidence_refs,
        )
    except StructuredOutputError as exc:
        draft = _numeric_candidate(exc.candidate)
        if draft is None:
            empty = _empty_numeric_assembly(
                node=node,
                status=NumericAuditStatus.INCOMPLETE,
            )
            return replace(
                empty,
                audit=_numeric_audit_appendix(
                    status=NumericAuditAppendixStatus.INCOMPLETE,
                    failures=exc.failures,
                    omissions=(
                        NumericAuditOmission(
                            component_path="numeric.appendix",
                            label="Optional numeric appendix",
                            issue_codes=tuple(
                                dict.fromkeys(
                                    issue
                                    for failure in exc.failures
                                    for issue in failure.validation_issues
                                )
                            )
                            or ("numeric.appendix.invalid",),
                        ),
                    ),
                ),
            )
        assembly = _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs=allowed,
            salvage=True,
            node=node,
        )
        return replace(
            assembly,
            audit=_numeric_audit_appendix(
                status=(
                    NumericAuditAppendixStatus.PARTIAL
                    if assembly.status is NumericAuditStatus.PARTIAL
                    else NumericAuditAppendixStatus.INCOMPLETE
                ),
                failures=exc.failures,
                omissions=assembly.omissions,
            ),
        )
    assembly = _assemble_numeric_draft(
        output.value,
        bundle=bundle,
        allowed_evidence_refs=allowed,
        salvage=False,
        node=node,
    )
    if output.failed_attempts:
        return replace(
            assembly,
            audit=_numeric_audit_appendix(
                status=NumericAuditAppendixStatus.RECOVERED,
                failures=output.failed_attempts,
                omissions=(),
            ),
        )
    return assembly


def _numeric_candidate(candidate: dict[str, Any] | None) -> DecisionNumericDraft | None:
    if candidate is None:
        return None
    try:
        return DecisionNumericDraft.model_validate(candidate)
    except Exception:
        return None


_NUMERIC_CANDIDATE_MAX_BYTES = 256 * 1024
_SENSITIVE_CANDIDATE_KEY = re.compile(
    r"(?i)(api.?key|authorization|bearer|password|secret|token)"
)
_SENSITIVE_CANDIDATE_VALUE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)"
    r"(\s*[:=]\s*)(\S+)"
)


def _numeric_audit_appendix(
    *,
    status: NumericAuditAppendixStatus,
    failures: tuple[StructuredOutputFailure, ...],
    omissions: tuple[NumericAuditOmission, ...],
) -> DecisionNumericAuditAppendix:
    snapshots = tuple(_numeric_audit_snapshot(failure) for failure in failures)
    if not snapshots:
        raise ValueError("numeric audit appendix requires a failed attempt")
    return DecisionNumericAuditAppendix(
        status=status,
        snapshots=snapshots[-2:],
        omitted_components=omissions,
    )


def _numeric_audit_snapshot(
    failure: StructuredOutputFailure,
) -> NumericAuditSnapshot:
    candidate = _sanitize_numeric_candidate(failure.candidate)
    if candidate is None:
        return NumericAuditSnapshot(
            phase=NumericAuditPhase(failure.phase),
            method=failure.method,
            reason_code=failure.reason_code,
            validation_issues=failure.validation_issues,
            schema_valid=False,
        )
    encoded = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    schema_valid = _numeric_candidate(candidate) is not None
    if len(encoded) > _NUMERIC_CANDIDATE_MAX_BYTES:
        return NumericAuditSnapshot(
            phase=NumericAuditPhase(failure.phase),
            method=failure.method,
            reason_code=failure.reason_code,
            validation_issues=failure.validation_issues,
            schema_valid=schema_valid,
            candidate_digest=digest,
            candidate_omitted="oversize",
        )
    return NumericAuditSnapshot(
        phase=NumericAuditPhase(failure.phase),
        method=failure.method,
        reason_code=failure.reason_code,
        validation_issues=failure.validation_issues,
        schema_valid=schema_valid,
        candidate=candidate,
        candidate_digest=digest,
    )


def _sanitize_numeric_candidate(
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if candidate is None:
        return None

    def sanitize(value: Any, key: str | None = None) -> Any:
        if key is not None and _SENSITIVE_CANDIDATE_KEY.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                str(item_key): sanitize(item_value, str(item_key))
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, str):
            return _SENSITIVE_CANDIDATE_VALUE.sub(
                r"\1\2[REDACTED]",
                value,
            )
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return str(value)

    sanitized = sanitize(candidate)
    return sanitized if isinstance(sanitized, dict) else None


def _empty_numeric_assembly(
    *,
    node: str,
    status: NumericAuditStatus,
) -> _NumericDecisionAssembly:
    return _NumericDecisionAssembly(
        scenario_valuations={},
        valuation_assessment=None,
        market_reference_levels=(),
        calculation_records=(),
        status=status,
        warnings=(
            ResearchWarning(
                code=f"decision.numeric_audit_{status.value}",
                message=(
                    "Optional valuation and market-reference figures were "
                    "omitted because their calculations could not be fully "
                    "validated. The qualitative decision remains audited."
                ),
                source=node,
            ),
        ),
    )


def _assemble_numeric_draft(
    draft: DecisionNumericDraft,
    *,
    bundle: EvidenceBundle,
    allowed_evidence_refs: set[str],
    salvage: bool,
    node: str,
) -> _NumericDecisionAssembly:
    issues: list[str] = []
    calculations: dict[str, CalculationRecord] = {}
    evidence_dates = {item.ref: item.effective_date for item in bundle.items}
    duplicate_ids = {
        item.id
        for item in draft.calculation_records
        if sum(other.id == item.id for other in draft.calculation_records) > 1
    }
    for item in draft.calculation_records:
        prefix = f"numeric.calculation.{item.id}"
        if item.id in duplicate_ids:
            issues.append(f"{prefix}.duplicate_id")
            continue
        try:
            require_nonempty_texts(item.limitations)
            require_valid_refs(
                item.input_evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
            inputs = item.input_mapping()
            calculated = _evaluate_formula(
                item.formula,
                inputs,
                issue_prefix=prefix,
            )
            as_of_date = _latest_evidence_date(
                item.input_evidence_refs,
                evidence_dates=evidence_dates,
                analysis_date=bundle.analysis_date,
                issue_prefix=prefix,
            )
            calculations[item.id] = CalculationRecord(
                id=item.id,
                formula=item.formula,
                inputs=inputs,
                input_evidence_refs=item.input_evidence_refs,
                result=calculated,
                unit=item.unit,
                as_of_date=as_of_date,
                limitations=item.limitations,
            )
        except OutputValidationError as exc:
            issues.append(exc.issue_code)

    scenario_values: dict[ResearchScenarioKind, ScenarioValuationDraft] = {}
    linked_ids: set[str] = set()
    seen_scenarios: set[ResearchScenarioKind] = set()
    for scenario in draft.scenario_valuations:
        prefix = f"numeric.scenario.{scenario.kind.value}"
        if scenario.kind in seen_scenarios:
            issues.append(f"{prefix}.duplicate")
            continue
        seen_scenarios.add(scenario.kind)
        if _valid_component_calculations(
            scenario.calculation_ids,
            calculations=calculations,
            issues=issues,
            prefix=prefix,
        ):
            scenario_values[scenario.kind] = scenario
            linked_ids.update(scenario.calculation_ids)

    valuation: ValuationAssessment | None = None
    if draft.valuation_assessment is not None:
        item = draft.valuation_assessment
        prefix = "numeric.valuation"
        valid = True
        as_of_date: date | None = None
        try:
            require_valid_refs(
                item.input_evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
            require_nonempty_texts(item.limitations)
        except OutputValidationError as exc:
            issues.append(f"{prefix}.{exc.issue_code}")
            valid = False
        if not _valid_component_calculations(
            item.calculation_ids,
            calculations=calculations,
            issues=issues,
            prefix=prefix,
        ):
            valid = False
        if valid:
            try:
                as_of_date = _latest_component_date(
                    evidence_refs=item.input_evidence_refs,
                    calculation_ids=item.calculation_ids,
                    evidence_dates=evidence_dates,
                    calculations=calculations,
                    analysis_date=bundle.analysis_date,
                    issue_prefix=prefix,
                )
            except OutputValidationError as exc:
                issues.append(exc.issue_code)
                valid = False
        if valid:
            assert as_of_date is not None
            valuation = ValuationAssessment(
                method=item.method,
                valuation_range=item.valuation_range,
                currency=item.currency,
                as_of_date=as_of_date,
                input_evidence_refs=item.input_evidence_refs,
                limitations=item.limitations,
                calculation_ids=item.calculation_ids,
            )
            linked_ids.update(item.calculation_ids)

    reference_levels: list[MarketReferenceLevel] = []
    for index, item in enumerate(draft.market_reference_levels):
        prefix = f"numeric.market_reference.{index}"
        valid = True
        as_of_date: date | None = None
        try:
            require_text(item.interpretation)
            require_valid_refs(
                item.evidence_refs,
                allowed_evidence_refs,
                required=True,
            )
        except OutputValidationError as exc:
            issues.append(f"{prefix}.{exc.issue_code}")
            valid = False
        if item.basis is MarketReferenceBasis.OBSERVED:
            if item.calculation_ids:
                issues.append(f"{prefix}.observed_has_calculation")
                valid = False
        elif not _valid_component_calculations(
            item.calculation_ids,
            calculations=calculations,
            issues=issues,
            prefix=prefix,
        ):
            valid = False
        if valid:
            try:
                as_of_date = _latest_component_date(
                    evidence_refs=item.evidence_refs,
                    calculation_ids=item.calculation_ids,
                    evidence_dates=evidence_dates,
                    calculations=calculations,
                    analysis_date=bundle.analysis_date,
                    issue_prefix=prefix,
                )
            except OutputValidationError as exc:
                issues.append(exc.issue_code)
                valid = False
        if valid:
            assert as_of_date is not None
            reference_levels.append(
                MarketReferenceLevel(
                    label=item.label,
                    value=item.value,
                    unit=item.unit,
                    as_of_date=as_of_date,
                    interpretation=item.interpretation,
                    evidence_refs=item.evidence_refs,
                    basis=item.basis,
                    calculation_ids=item.calculation_ids,
                )
            )
            linked_ids.update(item.calculation_ids)

    orphaned = set(calculations).difference(linked_ids)
    for calculation_id in sorted(orphaned):
        issues.append(f"numeric.calculation.{calculation_id}.orphaned")
    if draft.requested and not (
        scenario_values or valuation is not None or reference_levels
    ):
        issues.append("numeric.requested.empty")
    if not draft.requested and (
        draft.scenario_valuations
        or draft.valuation_assessment is not None
        or draft.market_reference_levels
        or draft.calculation_records
    ):
        issues.append("numeric.not_requested.has_content")

    if issues and not salvage:
        raise OutputValidationError(
            issues[0],
            issue_codes=tuple(issues),
        )

    kept_calculations = tuple(
        calculation
        for calculation_id, calculation in calculations.items()
        if calculation_id in linked_ids
    )
    has_content = bool(
        scenario_values or valuation is not None or reference_levels
    )
    omissions = _numeric_omissions(draft, tuple(issues))
    if issues:
        status = (
            NumericAuditStatus.PARTIAL
            if has_content
            else NumericAuditStatus.INCOMPLETE
        )
        omitted = ", ".join(item.label for item in omissions)
        warnings = (
            ResearchWarning(
                code=f"decision.numeric_audit_{status.value}",
                message=(
                    "Optional numeric components were omitted because their "
                    "audit failed"
                    + (f": {omitted}." if omitted else ".")
                    + " The qualitative decision remains audited."
                ),
                source=node,
            ),
        )
    else:
        status = (
            NumericAuditStatus.COMPLETE
            if has_content
            else NumericAuditStatus.NOT_APPLICABLE
        )
        warnings = ()
    return _NumericDecisionAssembly(
        scenario_valuations=scenario_values,
        valuation_assessment=valuation,
        market_reference_levels=tuple(reference_levels),
        calculation_records=kept_calculations,
        status=status,
        warnings=warnings,
        issues=tuple(issues),
        omissions=omissions,
    )


def _numeric_omissions(
    draft: DecisionNumericDraft,
    issues: tuple[str, ...],
) -> tuple[NumericAuditOmission, ...]:
    grouped: dict[tuple[str, str], list[str]] = {}
    reference_labels = {
        str(index): item.label
        for index, item in enumerate(draft.market_reference_levels)
    }
    for issue in issues:
        parts = issue.split(".")
        path = "numeric.appendix"
        label = "Optional numeric appendix"
        if len(parts) >= 4 and parts[:2] == ["numeric", "calculation"]:
            path = ".".join(parts[:3])
            label = parts[2]
        elif len(parts) >= 4 and parts[:2] == ["numeric", "scenario"]:
            path = ".".join(parts[:3])
            label = f"{parts[2].title()} scenario range"
        elif parts[:2] == ["numeric", "valuation"]:
            path = "numeric.valuation"
            label = "Valuation assessment"
        elif len(parts) >= 4 and parts[:2] == ["numeric", "market_reference"]:
            path = ".".join(parts[:3])
            label = reference_labels.get(parts[2], f"Market reference {parts[2]}")
        grouped.setdefault((path, label), []).append(issue)
    return tuple(
        NumericAuditOmission(
            component_path=path,
            label=label,
            issue_codes=tuple(dict.fromkeys(component_issues)),
        )
        for (path, label), component_issues in grouped.items()
    )


def _valid_component_calculations(
    calculation_ids: tuple[str, ...],
    *,
    calculations: Mapping[str, CalculationRecord],
    issues: list[str],
    prefix: str,
) -> bool:
    if not calculation_ids:
        issues.append(f"{prefix}.missing_calculation")
        return False
    if len(calculation_ids) != len(set(calculation_ids)):
        issues.append(f"{prefix}.duplicate_calculation")
        return False
    missing = [item for item in calculation_ids if item not in calculations]
    if missing:
        issues.append(f"{prefix}.unknown_calculation")
        return False
    return True


def _latest_evidence_date(
    evidence_refs: tuple[str, ...],
    *,
    evidence_dates: Mapping[str, date | None],
    analysis_date: date,
    issue_prefix: str,
) -> date:
    dates: list[date] = []
    for evidence_ref in evidence_refs:
        effective_date = evidence_dates.get(evidence_ref)
        if effective_date is None:
            raise OutputValidationError(f"{issue_prefix}.date_unavailable")
        if effective_date > analysis_date:
            raise OutputValidationError(f"{issue_prefix}.future_date")
        dates.append(effective_date)
    if not dates:
        raise OutputValidationError(f"{issue_prefix}.date_unavailable")
    return max(dates)


def _latest_component_date(
    *,
    evidence_refs: tuple[str, ...],
    calculation_ids: tuple[str, ...],
    evidence_dates: Mapping[str, date | None],
    calculations: Mapping[str, CalculationRecord],
    analysis_date: date,
    issue_prefix: str,
) -> date:
    direct_date = _latest_evidence_date(
        evidence_refs,
        evidence_dates=evidence_dates,
        analysis_date=analysis_date,
        issue_prefix=issue_prefix,
    )
    dates = [direct_date]
    dates.extend(calculations[item].as_of_date for item in calculation_ids)
    result = max(dates)
    if result > analysis_date:
        raise OutputValidationError(f"{issue_prefix}.future_date")
    return result


def debate_round_has_material_progress(
    state: Mapping[str, Any],
    *,
    round_number: int,
) -> bool:
    """Continue only when the set of material open issues actually changes."""

    rebuttals = [
        RebuttalReview.model_validate(raw)
        for raw in state.get("rebuttals", [])
    ]
    current = [
        item for item in rebuttals if item.round == round_number
    ]
    if not current:
        return False
    current_open = {
        issue_id
        for item in current
        for issue_id in item.open_issue_ids
    }
    if not current_open:
        return False
    prior = [
        item for item in rebuttals if item.round < round_number
    ]
    if not prior:
        return True
    prior_open = {
        issue_id
        for item in prior
        for issue_id in item.open_issue_ids
    }
    return current_open != prior_open


def _runner(
    llm: Any,
    schema: Any,
    validator: Callable[[Any], Any],
    node: str,
    event_writer: EventWriter | None,
) -> StructuredOutputRunner[Any]:
    return StructuredOutputRunner(
        llm=llm,
        schema=schema,
        validator=validator,
        node=node,
        event_writer=event_writer,
        invoke_config={"metadata": {"research_node": node}},
        repair_mode="preferred",
        include_candidate_in_repair=True,
        repair_instructions=(
            "Repair only invalid shallow routing metadata such as issue IDs, "
            "confidence, or dispositions. The readable Markdown is already "
            "complete and must not be regenerated."
        ),
    )


def _evidence_refs(state: Mapping[str, Any]) -> tuple[str, ...]:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    return tuple(item.ref for item in bundle.items)


def _mentioned_ids(markdown: str, valid_ids: set[str]) -> tuple[str, ...]:
    """Return exact valid IDs in first-appearance order."""

    matches: list[tuple[int, str]] = []
    for candidate in valid_ids:
        match = re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(candidate)}"
            r"(?![A-Za-z0-9_-])",
            markdown,
        )
        if match is not None:
            matches.append((match.start(), candidate))
    return tuple(candidate for _, candidate in sorted(matches))


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _is_truncated(response: Any) -> bool:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict) and isinstance(response, dict):
        metadata = response.get("response_metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("finish_reason") in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }


def _evaluate_formula(
    formula: str,
    inputs: Mapping[str, int | float],
    *,
    issue_prefix: str = "calculation",
) -> float:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise OutputValidationError(
            f"{issue_prefix}.formula.invalid_syntax"
        ) from exc

    referenced_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    missing_names = referenced_names.difference(inputs)
    if missing_names:
        raise OutputValidationError(
            f"{issue_prefix}.formula.missing_input"
        )
    unused_names = set(inputs).difference(referenced_names)
    if unused_names:
        raise OutputValidationError(
            f"{issue_prefix}.formula.unused_input"
        )

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value,
                (int, float),
            ):
                raise OutputValidationError(
                    f"{issue_prefix}.formula.non_numeric_constant"
                )
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in inputs:
                raise OutputValidationError(
                    f"{issue_prefix}.formula.missing_input"
                )
            return float(inputs[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op,
            (ast.UAdd, ast.USub),
        ):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise OutputValidationError(
                        f"{issue_prefix}.formula.division_by_zero"
                    )
                return left / right
            if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                try:
                    return left**right
                except OverflowError as exc:
                    raise OutputValidationError(
                        f"{issue_prefix}.formula.overflow"
                    ) from exc
        raise OutputValidationError(
            f"{issue_prefix}.formula.unsupported_operation"
        )

    result = evaluate(tree)
    if not math.isfinite(result):
        raise OutputValidationError(
            f"{issue_prefix}.formula.non_finite_result"
        )
    return result
