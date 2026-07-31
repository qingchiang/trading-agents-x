"""Readable research deliberation with shallow routing contracts."""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.application.contracts import (
    AnalystReport,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    EvidenceBundle,
    IssueDisposition,
    JudgeDraft,
    MemoryContext,
    RebuttalReview,
    ResearchCase,
    ResearchDecision,
    ResearchRating,
    ResearchScenario,
    ResearchScenarioKind,
    RiskReview,
    RiskReviewAdjustment,
    RiskReviewDisposition,
)
from tradingagents.graph.evidence_context import (
    PreparedEvidence,
    build_evidence_catalog,
    prepared_evidence_prompt,
)
from tradingagents.graph.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputResult,
    StructuredOutputRunner,
)

EventWriter = Callable[[dict[str, Any]], None]


class ResearchCaseAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus_claim_ids: tuple[str, ...] = ()
    report_section_refs: tuple[str, ...] = ()


class RebuttalAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addressed_issue_ids: tuple[str, ...] = Field(min_length=1)
    open_issue_ids: tuple[str, ...] = ()


class JudgeAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preliminary_rating: ResearchRating
    confidence: float = Field(ge=0.0, le=1.0)
    issue_dispositions: tuple[IssueDisposition, ...] = Field(min_length=1)


class RiskAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenged_issue_ids: tuple[str, ...] = ()
    unresolved_issue_ids: tuple[str, ...] = ()


def write_research_markdown(
    llm: Any,
    *,
    prompt: str,
    node: str,
    invoke_config: dict[str, Any] | None = None,
) -> str:
    """Generate one readable deliberation document without a JSON contract."""

    response = llm.invoke(
        prompt
        + "\n\nWrite the complete research reasoning as readable Markdown. "
        "Use headings, concise tables, and evidence footnotes where they help "
        "the reader. Do not emit JSON, schema fields, or hidden chain-of-thought.",
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
    return markdown


def research_prompt(
    state: Mapping[str, Any],
    *,
    title: str,
    objective: str,
    extra: str,
    memory: MemoryContext | None = None,
    prepared_evidence: PreparedEvidence | None = None,
) -> str:
    """Render readable reports plus a compact, query-backed evidence workset."""

    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    reports = {
        key: AnalystReport.model_validate(value).model_dump(mode="json")
        for key, value in state["analyst_reports"].items()
    }
    memory_text = memory.prompt_text() if memory is not None else ""
    memory_section = (
        "HISTORICAL FEEDBACK MEMORY (NOT CURRENT EVIDENCE):\n" + memory_text
        if memory_text
        else "HISTORICAL FEEDBACK MEMORY: none supplied"
    )
    prepared = prepared_evidence or PreparedEvidence(
        catalog=build_evidence_catalog(bundle),
        memo=(
            "Use the complete readable analyst reports and compact evidence "
            "catalog. Request exact source material through read-only tools."
        ),
    )
    return f"""You are the {title} in an evidence-first investment research
system.

Objective:
{objective}

Research rules:
- Read every complete analyst Markdown report, including its tables. Do not
  reduce reports to extracted claims.
- Key claims are navigation and audit aids, not a substitute for the report.
- Evidence footnotes may be used for material facts, but do not cite every
  sentence or table cell.
- Never invent report section IDs, claim IDs, issue IDs, evidence refs, sources,
  dates, values, or portfolio context.
- Missing evidence is uncertainty, not a neutral or bearish signal.
- Historical memory may calibrate confidence, risks, and invalidation only; it
  is not current evidence.
- Non-personalized ratings, valuation scenarios, and market reference levels
  are allowed. Do not provide account allocation, position percentages, order
  quantities/types, or mandatory entry, stop, or take-profit instructions.
- Write human-readable Markdown in
  {state.get("output_language", "English")}. Keep schema enums and IDs unchanged.

ANALYST REPORTS:
{json.dumps(reports, ensure_ascii=False)}

EVIDENCE WORKSET:
{prepared_evidence_prompt(prepared)}

{memory_section}

ADDITIONAL CONTEXT:
{extra}
"""


def invoke_research_case(
    llm: Any,
    *,
    role: str,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[ResearchCase]:
    valid_claims = _claim_ids(state)
    valid_sections = _section_ids(state)

    def validate(result: ResearchCaseAudit) -> ResearchCaseAudit:
        if not set(result.focus_claim_ids).issubset(valid_claims):
            raise OutputValidationError("navigation.claim.unknown")
        if not set(result.report_section_refs).issubset(valid_sections):
            raise OutputValidationError("navigation.section.unknown")
        return result

    audited = _runner(
        llm,
        ResearchCaseAudit,
        validate,
        node,
        event_writer,
    ).invoke(
        (
            "Extract only shallow navigation metadata from this completed "
            f"{role} research case. Do not rewrite the Markdown.\n\n"
            f"MARKDOWN:\n{markdown}"
        ),
        example=ResearchCaseAudit(
            focus_claim_ids=tuple(sorted(valid_claims)[:1]),
            report_section_refs=tuple(sorted(valid_sections)[:1]),
        ).model_dump(mode="json"),
        allowed_evidence_refs=_evidence_refs(state),
    )
    return StructuredOutputResult(
        value=ResearchCase(
            role=role,
            markdown=markdown,
            focus_claim_ids=audited.value.focus_claim_ids,
            report_section_refs=audited.value.report_section_refs,
        ),
        generation_method=audited.generation_method,
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


def invoke_rebuttal(
    llm: Any,
    *,
    role: str,
    round_number: int,
    markdown: str,
    state: Mapping[str, Any],
    node: str,
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
            f"MARKDOWN:\n{markdown}"
        ),
        example=RebuttalAudit(
            addressed_issue_ids=(first_issue,),
            open_issue_ids=(first_issue,),
        ).model_dump(mode="json"),
        allowed_evidence_refs=_evidence_refs(state),
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
            f"MARKDOWN:\n{markdown}"
        ),
        example=JudgeAudit(
            preliminary_rating=ResearchRating.HOLD,
            confidence=0.55,
            issue_dispositions=example_dispositions,
        ).model_dump(mode="json"),
        allowed_evidence_refs=_evidence_refs(state),
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
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    valid_issues = {issue.id for issue in agenda.issues}

    def validate(result: RiskAudit) -> RiskAudit:
        if not set(result.challenged_issue_ids).issubset(valid_issues):
            raise OutputValidationError("navigation.issue.unknown_challenged")
        if not set(result.unresolved_issue_ids).issubset(valid_issues):
            raise OutputValidationError("navigation.issue.unknown_unresolved")
        return result

    first_issue = agenda.issues[0].id
    audited = _runner(
        llm,
        RiskAudit,
        validate,
        node,
        event_writer,
    ).invoke(
        (
            "Extract only challenged and unresolved DebateAgenda issue IDs "
            "from this completed risk review. Do not rewrite the Markdown.\n\n"
            f"MARKDOWN:\n{markdown}"
        ),
        example=RiskAudit(
            challenged_issue_ids=(first_issue,),
            unresolved_issue_ids=(first_issue,),
        ).model_dump(mode="json"),
        allowed_evidence_refs=_evidence_refs(state),
    )
    return StructuredOutputResult(
        value=RiskReview(
            role=role,
            markdown=markdown,
            challenged_issue_ids=audited.value.challenged_issue_ids,
            unresolved_issue_ids=audited.value.unresolved_issue_ids,
        ),
        generation_method=audited.generation_method,
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
) -> StructuredOutputResult[ResearchDecision]:
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

    def validate(result: ResearchDecision) -> ResearchDecision:
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
        if result.valuation_assessment is not None:
            if result.valuation_assessment.as_of_date > bundle.analysis_date:
                raise OutputValidationError("decision.valuation.future_date")
            require_valid_refs(
                result.valuation_assessment.input_evidence_refs,
                set(valid_refs),
                required=True,
            )
            require_nonempty_texts(result.valuation_assessment.limitations)
        for level in result.market_reference_levels:
            if level.as_of_date > bundle.analysis_date:
                raise OutputValidationError(
                    "decision.market_reference.future_date"
                )
            require_text(level.interpretation)
            require_valid_refs(
                level.evidence_refs,
                set(valid_refs),
                required=True,
            )
        for calculation in result.calculation_records:
            if calculation.as_of_date > bundle.analysis_date:
                raise OutputValidationError("decision.calculation.future_date")
            require_nonempty_texts(calculation.limitations)
            require_valid_refs(
                calculation.input_evidence_refs,
                set(valid_refs),
                required=True,
            )
            calculated = _evaluate_formula(
                calculation.formula,
                calculation.inputs,
            )
            if not math.isclose(
                calculated,
                float(calculation.result),
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise OutputValidationError(
                    "decision.calculation.result_mismatch"
                )
        if result.valuation_assessment is not None and not any(
            item.purpose.value == "valuation"
            for item in result.calculation_records
        ):
            raise OutputValidationError(
                "decision.calculation.valuation_missing"
            )
        if any(scenario.valuation_range is not None for scenario in result.scenarios) and not any(
            item.purpose.value == "scenario"
            for item in result.calculation_records
        ):
            raise OutputValidationError(
                "decision.calculation.scenario_missing"
            )
        if result.market_reference_levels and not any(
            item.purpose.value == "market_reference"
            for item in result.calculation_records
        ):
            raise OutputValidationError(
                "decision.calculation.market_reference_missing"
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

    return StructuredOutputRunner(
        llm=llm,
        schema=ResearchDecision,
        validator=validate,
        node=node,
        event_writer=event_writer,
        repair_mode="preferred",
        include_candidate_in_repair=True,
        invoke_config={"metadata": {"research_node": node}},
        repair_instructions=(
            "Keep valid research content. Use only allowed evidence and memory "
            "refs. Every decision-critical calculation must be reproducible "
            "from its named numeric inputs."
        ),
    ).invoke(
        prompt
        + "\n\nThe final decision is the strict audit boundary. Include only "
        "decision-critical calculations; ordinary report-table arithmetic does "
        "not belong in calculation_records.",
        example=ResearchDecision(
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
                ResearchScenario(
                    kind=ResearchScenarioKind.BASE,
                    core_assumptions=(
                        "Current evidence remains representative.",
                    ),
                    outcome="The thesis develops broadly as expected.",
                    evidence_refs=(first_ref,),
                ),
                ResearchScenario(
                    kind=ResearchScenarioKind.BULL,
                    core_assumptions=(
                        "The constructive mechanism strengthens.",
                    ),
                    outcome="The result exceeds the base case.",
                    evidence_refs=(first_ref,),
                ),
                ResearchScenario(
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
            "Repair only invalid shallow routing metadata such as claim IDs, "
            "section IDs, issue IDs, confidence, or dispositions. The readable "
            "Markdown is already complete and must not be regenerated."
        ),
    )


def _evidence_refs(state: Mapping[str, Any]) -> tuple[str, ...]:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    return tuple(item.ref for item in bundle.items)


def _claim_ids(state: Mapping[str, Any]) -> set[str]:
    return {
        claim.id
        for raw in state["analyst_reports"].values()
        for claim in AnalystReport.model_validate(raw).key_claims
    }


def _section_ids(state: Mapping[str, Any]) -> set[str]:
    return {
        section.id
        for raw in state["analyst_reports"].values()
        for section in AnalystReport.model_validate(raw).report_sections
    }


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
) -> float:
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise OutputValidationError(
            "calculation.formula.invalid_syntax"
        ) from exc

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value,
                (int, float),
            ):
                raise OutputValidationError(
                    "calculation.formula.non_numeric_constant"
                )
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in inputs:
                raise OutputValidationError(
                    "calculation.formula.unknown_input"
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
                        "calculation.formula.division_by_zero"
                    )
                return left / right
            if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                try:
                    return left**right
                except OverflowError as exc:
                    raise OutputValidationError(
                        "calculation.formula.overflow"
                    ) from exc
        raise OutputValidationError(
            "calculation.formula.unsupported_operation"
        )

    result = evaluate(tree)
    if not math.isfinite(result):
        raise OutputValidationError("calculation.formula.non_finite_result")
    return result
