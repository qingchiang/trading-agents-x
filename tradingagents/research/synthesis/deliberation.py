"""Readable cases, agenda, rebuttal, judge and risk deliberation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    DebateImportance,
    ResearchRating,
)
from tradingagents.domain.reports import (
    DebateAgenda,
    DebateIssue,
    IssueDisposition,
    JudgeDraft,
    RebuttalReview,
    ResearchCase,
    RiskReview,
)
from tradingagents.research.presentation import normalize_evidence_markdown
from tradingagents.research.synthesis.drafts import (
    EventWriter,
    JudgeAudit,
    RebuttalAudit,
    ResearchMarkdown,
)
from tradingagents.research.synthesis.generation import (
    _agenda_example_text,
    _evidence_refs,
    _is_standard_output_language,
    _is_truncated,
    _mentioned_ids,
    _message_text,
    _runner,
)
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
    require_text,
)
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
    StructuredOutputResult,
)


def write_research_markdown(
    llm: Any,
    *,
    prompt: str,
    node: str,
    allowed_evidence_refs: tuple[str, ...],
    output_language: str,
    allow_continuation: bool,
    invoke_config: dict[str, Any] | None = None,
) -> ResearchMarkdown:
    """Generate readable Markdown under an explicit continuation-call policy."""

    response = llm.invoke(
        prompt + "\n\nWrite the complete research reasoning as readable Markdown. "
        f"Write all human-readable prose in {output_language}. "
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
    if allow_continuation and _is_truncated(response):
        continuation = llm.invoke(
            (
                "Continue the prior Markdown from its last complete block. "
                "Do not repeat prior content and finish the document. Write all "
                f"human-readable prose in {output_language}."
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
        evidence_refs=normalized.evidence_refs,
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
    output_language: str,
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[DebateAgenda]:
    def validate(result: DebateAgenda) -> DebateAgenda:
        require_text(result.summary)
        for issue in result.issues:
            require_text(issue.question)
        return result

    example_text = _agenda_example_text(output_language)
    language_rule = (
        "Write the agenda summary and questions in this complete output-language "
        f"instruction: {output_language}. Keep issue IDs and importance enums in "
        "their required wire format."
    )
    example = DebateAgenda(
        summary=example_text["summary"],
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question=example_text["question"],
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    try:
        return _runner(
            llm,
            DebateAgenda,
            validate,
            node,
            event_writer,
            repair_instructions=(
                "Repair only the concise agenda object. Use distinct material "
                f"issues and preserve valid wire IDs. {language_rule}"
            ),
            candidate_only_repair=True,
        ).invoke(
            prompt + "\n\nReturn only a concise agenda summary and distinct material "
            "questions. The full bull and bear reasoning remains in their Markdown. "
            + language_rule
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(example.model_dump(mode="json"), ensure_ascii=False),
            example=example.model_dump(mode="json"),
            allowed_evidence_refs=_evidence_refs(state),
        )
    except StructuredOutputError:
        if not _is_standard_output_language(output_language):
            raise
        return StructuredOutputResult(
            value=DebateAgenda(
                summary=example_text["fallback_summary"],
                issues=(
                    DebateIssue(
                        id="debate.issue_audit_fallback",
                        question=example_text["fallback_question"],
                        importance=DebateImportance.MATERIAL,
                    ),
                ),
            ),
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
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
        if not addressed.issubset(valid_issues) or not opened.issubset(valid_issues):
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
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
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
    event_writer: EventWriter | None = None,
) -> StructuredOutputResult[JudgeDraft]:
    agenda = DebateAgenda.model_validate(state["debate_agenda"])
    issue_ids = {issue.id for issue in agenda.issues}

    def validate(result: JudgeAudit) -> JudgeAudit:
        actual = {item.issue_id for item in result.issue_dispositions}
        if actual != issue_ids:
            raise OutputValidationError("navigation.issue.disposition_incomplete")
        return result

    example_dispositions = tuple(
        IssueDisposition(issue_id=issue.id, status="unresolved") for issue in agenda.issues
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
            generation_method=(ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE),
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


def debate_round_has_material_progress(
    state: Mapping[str, Any],
    *,
    round_number: int,
) -> bool:
    """Continue only when the set of material open issues actually changes."""

    rebuttals = [RebuttalReview.model_validate(raw) for raw in state.get("rebuttals", [])]
    current = [item for item in rebuttals if item.round == round_number]
    if not current:
        return False
    current_open = {issue_id for item in current for issue_id in item.open_issue_ids}
    if not current_open:
        return False
    prior = [item for item in rebuttals if item.round < round_number]
    if not prior:
        return True
    prior_open = {issue_id for item in prior for issue_id in item.open_issue_ids}
    return current_open != prior_open
