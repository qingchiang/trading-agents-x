"""Readable research deliberation with shallow routing contracts."""

from __future__ import annotations

import ast
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
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
    ResearchWarning,
    RiskReview,
    RiskReviewAdjustment,
    RiskReviewDisposition,
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
