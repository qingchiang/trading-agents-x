"""Bounded post-Full disposition of persistent Research Questions."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field

from tradingagents.graph.output_validation import OutputValidationError
from tradingagents.graph.structured_output import StructuredOutputError, StructuredOutputRunner

from .research import (
    QuestionDispositionAudit,
    QuestionDispositionKind,
    QuestionDispositionLimitation,
    QuestionDispositionRecord,
    QuestionStatus,
    ResearchModel,
    ResearchRevisionDraft,
)


class _QuestionDispositionOutput(ResearchModel):
    schema_version: Literal["1"] = "1"
    language: str
    dispositions: tuple[QuestionDispositionRecord, ...] = Field(max_length=64)


_MAX_QUESTION_DISPOSITION_PROMPT_CHARS = 48_000


def run_full_question_disposition(
    baseline: ResearchRevisionDraft,
    candidate: ResearchRevisionDraft,
    llm: Any,
) -> ResearchRevisionDraft:
    """Record explicit baseline Question dispositions from sealed Full Evidence."""

    baseline_questions = {item.id: item for item in baseline.current_state.questions}
    if not baseline_questions:
        return candidate
    candidate_questions = {item.id: item for item in candidate.current_state.questions}
    current_refs = {item.ref for item in candidate.evidence_snapshot.bundle.items}

    def validate(value: _QuestionDispositionOutput) -> _QuestionDispositionOutput:
        if value.language != candidate.current_state.language:
            raise OutputValidationError("question_disposition.language_invalid")
        records = value.dispositions
        baseline_ids = tuple(item.baseline_question_id for item in records)
        if len(baseline_ids) != len(set(baseline_ids)) or set(baseline_ids) != set(
            baseline_questions
        ):
            raise OutputValidationError("question_disposition.incomplete")
        assigned_candidates: list[str] = []
        for item in records:
            candidate_ids = tuple(
                value
                for value in (item.candidate_question_id, item.successor_question_id)
                if value is not None
            )
            assigned_candidates.extend(candidate_ids)
            if not set(item.evidence_refs).issubset(current_refs):
                raise OutputValidationError("question_disposition.evidence_invalid")
            if any(value not in candidate_questions for value in candidate_ids):
                raise OutputValidationError("question_disposition.ambiguous_identity")
            if item.disposition is QuestionDispositionKind.SUPERSEDED:
                if item.successor_question_id is None or item.candidate_question_id is not None:
                    raise OutputValidationError("question_disposition.ambiguous_identity")
            elif item.successor_question_id is not None:
                raise OutputValidationError("question_disposition.ambiguous_identity")
            if (
                item.disposition is QuestionDispositionKind.REOPENED
                and baseline_questions[item.baseline_question_id].status
                is not QuestionStatus.ANSWERED
            ):
                raise OutputValidationError("question_disposition.ambiguous_identity")
        if len(assigned_candidates) != len(set(assigned_candidates)):
            raise OutputValidationError("question_disposition.ambiguous_identity")
        return value

    prompt = (
        "Dispose every baseline Research Question using only the sealed Full Evidence and "
        "application-assigned Question IDs. Omission is not a disposition. Return only the "
        "schema-constrained result.\n\nBOUNDED INPUT:\n"
        + json.dumps(
            {
                "baseline_questions": tuple(
                    item.model_dump(mode="json") for item in baseline_questions.values()
                ),
                "candidate_questions": tuple(
                    item.model_dump(mode="json") for item in candidate_questions.values()
                ),
                "current_evidence_refs": tuple(sorted(current_refs)),
                "output_language": candidate.current_state.language,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if len(prompt) > _MAX_QUESTION_DISPOSITION_PROMPT_CHARS:
        audit = QuestionDispositionAudit(
            status="limited",
            language=candidate.current_state.language,
            limitation_reason=QuestionDispositionLimitation.OUTPUT_INVALID,
        )
        return candidate.model_copy(
            update={"delta": candidate.delta.model_copy(update={"question_disposition": audit})}
        )
    first_question = next(iter(baseline_questions.values()))
    first_ref = next(iter(current_refs))
    example = {
        "language": candidate.current_state.language,
        "dispositions": (
            {
                "baseline_question_id": first_question.id,
                "disposition": "reaffirmed",
                "evidence_refs": (first_ref,),
                "reason": "Current Full Evidence supports keeping the Question open.",
            },
        ),
    }
    runner = StructuredOutputRunner(
        llm=llm,
        schema=_QuestionDispositionOutput,
        validator=validate,
        node="research.full.question_disposition",
        invoke_config={"metadata": {"research_node": "research.full.question_disposition"}},
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
        repair_instructions=(
            "Cover every supplied baseline Question exactly once. Use only supplied Question "
            "and current Evidence identifiers."
        ),
    )
    try:
        result = runner.invoke(
            prompt,
            example=example,
            allowed_evidence_refs=tuple(sorted(current_refs)),
        )
    except StructuredOutputError as exc:
        issue_codes = {issue.removeprefix("semantic.") for issue in exc.validation_issues}
        if "question_disposition.evidence_invalid" in issue_codes:
            reason = QuestionDispositionLimitation.EVIDENCE_INVALID
        elif "question_disposition.ambiguous_identity" in issue_codes:
            reason = QuestionDispositionLimitation.AMBIGUOUS_IDENTITY
        elif "question_disposition.incomplete" in issue_codes:
            reason = QuestionDispositionLimitation.INCOMPLETE
        else:
            reason = QuestionDispositionLimitation.OUTPUT_INVALID
        audit = QuestionDispositionAudit(
            status="limited",
            language=candidate.current_state.language,
            limitation_reason=reason,
            repair_attempted=True,
        )
    else:
        audit = QuestionDispositionAudit(
            status="complete",
            language=result.value.language,
            dispositions=tuple(
                QuestionDispositionRecord.model_validate(item) for item in result.value.dispositions
            ),
            repair_attempted=bool(result.failed_attempts),
        )
    return candidate.model_copy(
        update={"delta": candidate.delta.model_copy(update={"question_disposition": audit})}
    )
