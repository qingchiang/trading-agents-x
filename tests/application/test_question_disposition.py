from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.messages import AIMessage

from tests.application.test_service import _execution
from tradingagents.application.contracts import AnalysisRequest
from tradingagents.application.question_disposition import run_full_question_disposition
from tradingagents.application.research import (
    CoverageStatus,
    QuestionChange,
    QuestionDispositionLimitation,
    QuestionStatus,
    ResearchChangeConclusion,
    ResearchObjectCoverage,
    ResearchQuestion,
    assemble_full_revision,
    assemble_full_update,
)


class _Invoker:
    def __init__(self, response: Any, prompts: list[str]):
        self.response = response
        self.prompts = prompts

    def invoke(self, prompt: str, config: Any = None) -> Any:
        del config
        self.prompts.append(prompt)
        return self.response


class _LLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, *responses: Any):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def with_structured_output(self, _schema: Any, **kwargs: Any) -> _Invoker:
        assert kwargs["include_raw"] is True
        return _Invoker(self.responses.pop(0), self.prompts)


def _response(*dispositions: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw": AIMessage(content=""),
        "parsed": {
            "language": "en",
            "dispositions": dispositions,
        },
        "parsing_error": None,
    }


def _baseline_and_candidate():
    baseline = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=date(2026, 7, 24),
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    retained_claim_ids = set(baseline.current_state.opinion.primary_claim_ids)
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "claims": tuple(
                        item
                        for item in baseline.current_state.claims
                        if item.id in retained_claim_ids
                    ),
                    "scenarios": tuple(
                        item.model_copy(
                            update={
                                "assumption_claim_ids": tuple(retained_claim_ids),
                            }
                        )
                        for item in baseline.current_state.scenarios
                    ),
                    "risks": (),
                    "catalysts": (),
                    "invalidation_conditions": (),
                }
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "claims": tuple(
                        item
                        for item in baseline.coverage.claims
                        if item.object_id in retained_claim_ids
                    )
                }
            ),
        }
    )
    question = ResearchQuestion(
        id="question_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        question="Will orders remain durable?",
        status=QuestionStatus.OPEN,
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(update={"questions": (question,)}),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    )
                }
            ),
        }
    )
    candidate = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=date(2026, 7, 25),
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    candidate = candidate.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "cutoff": candidate.cutoff,
                    "questions": (),
                    "scenarios": tuple(
                        item.model_copy(update={"cutoff": candidate.cutoff})
                        for item in baseline.current_state.scenarios
                    ),
                    "evidence_refs": candidate.current_state.evidence_refs,
                }
            ),
            "coverage": candidate.coverage.model_copy(
                update={
                    "claims": baseline.coverage.claims,
                    "questions": (),
                }
            ),
        }
    )
    return baseline, candidate, question


def test_answered_question_keeps_application_identity_and_current_evidence():
    baseline, candidate, question = _baseline_and_candidate()
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref
    llm = _LLM(
        _response(
            {
                "baseline_question_id": question.id,
                "disposition": "answered",
                "evidence_refs": (evidence_ref,),
                "reason": "The Full snapshot contains the reported order conversion.",
            }
        )
    )

    disposed = run_full_question_disposition(baseline, candidate, llm)
    updated = assemble_full_update("revision-1", baseline, disposed)

    resolved = next(item for item in updated.current_state.questions if item.id == question.id)
    delta = next(item for item in updated.delta.questions if item.object_id == question.id)
    assert resolved.status is QuestionStatus.ANSWERED
    assert resolved.evidence_refs == (evidence_ref,)
    assert delta.change is QuestionChange.ANSWERED
    assert delta.evidence_refs == (evidence_ref,)
    assert delta.reason == "The Full snapshot contains the reported order conversion."
    assert updated.delta.question_disposition is not None
    assert updated.delta.question_disposition.status == "complete"
    assert disposed.current_state.questions == ()
    assert "current_evidence" in llm.prompts[0]
    assert candidate.evidence_snapshot.bundle.items[0].source in llm.prompts[0]
    assert str(candidate.evidence_snapshot.bundle.items[0].requested_date) in llm.prompts[0]


def test_reaffirmed_question_is_not_resolved_by_full_decision_omission():
    baseline, candidate, question = _baseline_and_candidate()
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(
            _response(
                {
                    "baseline_question_id": question.id,
                    "disposition": "reaffirmed",
                    "evidence_refs": (evidence_ref,),
                    "reason": "Current Full Evidence leaves the uncertainty open.",
                }
            )
        ),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    retained = next(item for item in updated.current_state.questions if item.id == question.id)
    assert retained.status is QuestionStatus.OPEN
    assert (
        next(item for item in updated.delta.questions if item.object_id == question.id).change
        is QuestionChange.REAFFIRMED
    )


def test_answered_question_can_reopen_with_the_same_identity():
    baseline, candidate, question = _baseline_and_candidate()
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={
                    "questions": (question.model_copy(update={"status": QuestionStatus.ANSWERED}),)
                }
            )
        }
    )
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(
            _response(
                {
                    "baseline_question_id": question.id,
                    "disposition": "reopened",
                    "evidence_refs": (evidence_ref,),
                    "reason": "Current Evidence undermines the earlier answer.",
                }
            )
        ),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    reopened = next(item for item in updated.current_state.questions if item.id == question.id)
    assert reopened.status is QuestionStatus.OPEN
    assert reopened.last_disposition == "reopened"
    assert reopened.disposition_reason == "Current Evidence undermines the earlier answer."
    assert (
        next(item for item in updated.delta.questions if item.object_id == question.id).change
        is QuestionChange.REOPENED
    )


def test_superseded_question_links_a_separately_assigned_successor():
    baseline, candidate, question = _baseline_and_candidate()
    successor = ResearchQuestion(
        id="question_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        question="Will converted orders produce durable cash flow?",
    )
    candidate = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(update={"questions": (successor,)}),
            "coverage": candidate.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=successor.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    )
                }
            ),
        }
    )
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(
            _response(
                {
                    "baseline_question_id": question.id,
                    "disposition": "superseded",
                    "successor_question_id": successor.id,
                    "evidence_refs": (evidence_ref,),
                    "reason": "The narrower successor captures the remaining uncertainty.",
                }
            )
        ),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    superseded = next(item for item in updated.current_state.questions if item.id == question.id)
    assert superseded.status is QuestionStatus.SUPERSEDED
    assert superseded.successor_question_id == successor.id
    assert next(item for item in updated.current_state.questions if item.id == successor.id)
    delta = next(item for item in updated.delta.questions if item.object_id == question.id)
    assert delta.change is QuestionChange.SUPERSEDED
    assert delta.successor_object_id == successor.id


def test_retired_question_requires_an_explicit_supported_disposition():
    baseline, candidate, question = _baseline_and_candidate()
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(
            _response(
                {
                    "baseline_question_id": question.id,
                    "disposition": "retired",
                    "evidence_refs": (evidence_ref,),
                    "reason": "The business exited the activity that made the question relevant.",
                }
            )
        ),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    retired = next(item for item in updated.current_state.questions if item.id == question.id)
    assert retired.status is QuestionStatus.RETIRED
    assert (
        next(item for item in updated.delta.questions if item.object_id == question.id).change
        is QuestionChange.RETIRED
    )


def test_invalid_current_evidence_gets_one_repair_then_preserves_baseline_status():
    baseline, candidate, question = _baseline_and_candidate()
    invalid = _response(
        {
            "baseline_question_id": question.id,
            "disposition": "answered",
            "evidence_refs": ("ev_ffffffffffff",),
            "reason": "This reference is outside the sealed Full snapshot.",
        }
    )
    llm = _LLM(invalid, invalid)

    disposed = run_full_question_disposition(baseline, candidate, llm)
    updated = assemble_full_update("revision-1", baseline, disposed)

    assert llm.responses == []
    retained = next(item for item in updated.current_state.questions if item.id == question.id)
    assert retained.status is QuestionStatus.OPEN
    assert updated.delta.question_disposition is not None
    assert updated.delta.question_disposition.status == "limited"
    assert (
        updated.delta.question_disposition.limitation_reason
        is QuestionDispositionLimitation.EVIDENCE_INVALID
    )
    assert updated.coverage.questions[-1].status is CoverageStatus.LIMITED
    assert updated.change_conclusion is ResearchChangeConclusion.INDETERMINATE


def test_omitted_baseline_mapping_preserves_it_and_keeps_full_question_new():
    baseline, candidate, question = _baseline_and_candidate()
    candidate_question = ResearchQuestion(
        id="question_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        question="Is a different uncertainty material?",
    )
    candidate = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(
                update={"questions": (candidate_question,)}
            ),
            "coverage": candidate.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=candidate_question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    )
                }
            ),
        }
    )
    incomplete = _response()

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(incomplete, incomplete),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    assert {item.id for item in updated.current_state.questions} == {
        question.id,
        candidate_question.id,
    }
    assert (
        next(item for item in updated.current_state.questions if item.id == question.id).status
        is QuestionStatus.OPEN
    )
    assert (
        next(
            item for item in updated.delta.questions if item.object_id == candidate_question.id
        ).change
        is QuestionChange.INTRODUCED
    )
    assert (
        updated.delta.question_disposition.limitation_reason
        is QuestionDispositionLimitation.INCOMPLETE
    )
    assert updated.change_conclusion is ResearchChangeConclusion.MATERIAL_CHANGE
    assert "questions" in updated.delta.changed_sections


def test_invalid_output_is_repaired_at_most_once():
    baseline, candidate, question = _baseline_and_candidate()
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(
            _response(),
            _response(
                {
                    "baseline_question_id": question.id,
                    "disposition": "answered",
                    "evidence_refs": (evidence_ref,),
                    "reason": "The repaired result uses current Full Evidence.",
                }
            ),
        ),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    assert updated.delta.question_disposition.status == "complete"
    assert updated.delta.question_disposition.repair_attempted is True
    assert (
        next(item for item in updated.current_state.questions if item.id == question.id).status
        is QuestionStatus.ANSWERED
    )


def test_ambiguous_candidate_mapping_preserves_every_baseline_identity():
    baseline, candidate, first = _baseline_and_candidate()
    second = ResearchQuestion(
        id="question_cccccccccccccccccccccccccccccccc",
        question="Will margins remain durable?",
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"questions": (first, second)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        *baseline.coverage.questions,
                        ResearchObjectCoverage(
                            object_id=second.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    )
                }
            ),
        }
    )
    candidate_question = ResearchQuestion(
        id="question_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        question="Will the operating recovery remain durable?",
    )
    candidate = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(
                update={"questions": (candidate_question,)}
            ),
            "coverage": candidate.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=candidate_question.id,
                            status=CoverageStatus.LIMITED,
                        ),
                    )
                }
            ),
        }
    )
    evidence_ref = candidate.evidence_snapshot.bundle.items[0].ref
    ambiguous = _response(
        *(
            {
                "baseline_question_id": question.id,
                "disposition": "reaffirmed",
                "candidate_question_id": candidate_question.id,
                "evidence_refs": (evidence_ref,),
                "reason": "The same candidate was suggested twice.",
            }
            for question in (first, second)
        )
    )

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(ambiguous, ambiguous),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    statuses = {item.id: item.status for item in updated.current_state.questions}
    assert statuses[first.id] is QuestionStatus.OPEN
    assert statuses[second.id] is QuestionStatus.OPEN
    assert statuses[candidate_question.id] is QuestionStatus.OPEN
    assert (
        updated.delta.question_disposition.limitation_reason
        is QuestionDispositionLimitation.AMBIGUOUS_IDENTITY
    )


def test_question_limitation_does_not_override_independent_material_change():
    baseline, candidate, question = _baseline_and_candidate()
    candidate = candidate.model_copy(
        update={
            "current_state": candidate.current_state.model_copy(
                update={
                    "opinion": candidate.current_state.opinion.model_copy(
                        update={"thesis": "Independent Full research changed the thesis."}
                    )
                }
            )
        }
    )
    invalid = _response(
        {
            "baseline_question_id": question.id,
            "disposition": "retired",
            "evidence_refs": ("ev_ffffffffffff",),
            "reason": "The reference is invalid.",
        }
    )

    disposed = run_full_question_disposition(
        baseline,
        candidate,
        _LLM(invalid, invalid),
    )
    updated = assemble_full_update("revision-1", baseline, disposed)

    assert updated.change_conclusion is ResearchChangeConclusion.MATERIAL_CHANGE
    assert "question_disposition_evidence_invalid" in updated.coverage.limitations
    assert updated.coverage.supports_no_material_change is False
