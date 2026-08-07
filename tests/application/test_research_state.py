from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from tests.application.test_service import _execution
from tradingagents.application.contracts import (
    AnalysisRequest,
    ReportLanguage,
    ResearchRating,
    ResearchScenarioKind,
)
from tradingagents.application.research import (
    ClaimConfidence,
    ClaimStanding,
    CurrentResearchState,
    DecisionConfidence,
    DecisionRole,
    EpistemicKind,
    QuestionStatus,
    ResearchClaim,
    ResearchObjectCoverage,
    ResearchOpinion,
    ResearchQuestion,
    ResearchScenarioState,
    ScenarioLikelihood,
    assemble_full_revision,
    assemble_full_update,
)

REF = "ev_0123456789ab"
CUTOFF = date(2026, 7, 24)


def _claim(**updates: object) -> ResearchClaim:
    values = {
        "id": "claim_0123456789abcdef0123456789abcdef",
        "statement": "Margin recovery supports the thesis.",
        "epistemic_kind": EpistemicKind.INFERENCE,
        "decision_role": DecisionRole.THESIS,
        "standing": ClaimStanding.ACTIVE,
        "confidence": ClaimConfidence.MEDIUM,
        "evidence_refs": (REF,),
        "falsifier": "Reported margins remain below the prior-year level.",
    }
    values.update(updates)
    return ResearchClaim.model_validate(values)


def _state(*, scenarios: tuple[ResearchScenarioState, ...] | None = None):
    claim = _claim()
    scenario_values = scenarios or tuple(
        ResearchScenarioState(
            kind=kind,
            likelihood=ScenarioLikelihood.INDETERMINATE,
            cutoff=CUTOFF,
            horizon="12 months",
            outcome=f"{kind.value} outcome",
            assumption_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        )
        for kind in ResearchScenarioKind
    )
    return CurrentResearchState(
        language="en",
        instrument="6501.T",
        cutoff=CUTOFF,
        opinion=ResearchOpinion(
            rating=ResearchRating.HOLD,
            confidence=DecisionConfidence.MEDIUM,
            thesis="Evidence supports a conditional thesis.",
            primary_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        ),
        claims=(claim,),
        scenarios=scenario_values,
        evidence_refs=(REF,),
    )


def test_current_research_state_allows_tied_indeterminate_scenario_likelihoods():
    state = _state()

    assert {scenario.likelihood for scenario in state.scenarios} == {
        ScenarioLikelihood.INDETERMINATE
    }
    assert state.opinion.confidence is DecisionConfidence.MEDIUM
    assert state.claims[0].confidence is ClaimConfidence.MEDIUM


def test_current_research_state_requires_one_shared_scenario_horizon():
    claim = _claim()
    scenarios = tuple(
        ResearchScenarioState(
            kind=kind,
            likelihood=ScenarioLikelihood.LOW,
            cutoff=CUTOFF,
            horizon="24 months" if kind is ResearchScenarioKind.BULL else "12 months",
            outcome=f"{kind.value} outcome",
            assumption_claim_ids=(claim.id,),
            evidence_refs=(REF,),
        )
        for kind in ResearchScenarioKind
    )

    with pytest.raises(ValidationError, match="share horizon"):
        _state(scenarios=scenarios)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"epistemic_kind": EpistemicKind.OBSERVATION, "falsifier": None},
            "observed_at",
        ),
        ({"falsifier": None}, "falsifier"),
    ],
)
def test_research_claim_requires_observation_dates_or_falsifiers(updates, message):
    with pytest.raises(ValidationError, match=message):
        _claim(**updates)


def test_full_state_assembly_assigns_ids_and_preserves_selected_language():
    execution = _execution("6501.T")
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date=CUTOFF,
        analysts=("market",),
        output_language=ReportLanguage.JAPANESE,
    )

    draft = assemble_full_revision(request, execution)

    state = draft.current_state
    assert state.language == "ja"
    assert state.instrument == "6501.T"
    assert state.cutoff == CUTOFF
    assert state.opinion.primary_claim_ids[0].startswith("claim_")
    assert all(claim.id.startswith("claim_") for claim in state.claims)
    assert all(question.id.startswith("question_") for question in state.questions)
    assert draft.execution_strategy.value == "full"
    assert draft.coverage.domains[0].domain == "market"
    assert draft.evidence_snapshot.bundle.digest == execution.evidence.digest
    assert {item.lineage for item in draft.evidence_snapshot.lineage} == {"new"}
    assert draft.update_summary.language == "ja"


def test_full_state_assembly_rejects_missing_explicit_claim_evidence():
    execution = _execution("6501.T")
    report = execution.reports["market"].model_copy(
        update={
            "key_claims": tuple(
                claim.model_copy(update={"evidence_refs": ()})
                for claim in execution.reports["market"].key_claims
            )
        }
    )
    execution = execution.__class__(
        state=execution.state,
        evidence=execution.evidence,
        reports={"market": report},
        decision=execution.decision,
    )

    with pytest.raises(ValueError, match="explicit Evidence"):
        assemble_full_revision(
            AnalysisRequest(
                ticker="6501.T",
                analysis_date=CUTOFF,
                analysts=("market",),
            ),
            execution,
        )


def test_full_update_preserves_only_unambiguous_longitudinal_identities():
    request = AnalysisRequest(
        ticker="6501.T",
        analysis_date=date(2026, 7, 25),
        analysts=("market",),
    )
    baseline = assemble_full_revision(
        request.model_copy(update={"analysis_date": CUTOFF}),
        _execution("6501.T"),
    )
    candidate = assemble_full_revision(request, _execution("6501.T"))

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.claims[0].id == baseline.current_state.claims[0].id
    assert updated.delta.claims[0].change.value == "reaffirmed"
    assert {item.lineage for item in updated.evidence_snapshot.lineage} == {"new"}
    assert updated.execution_strategy.value == "full"


def test_full_update_does_not_reassign_ambiguous_claim_identity():
    baseline = assemble_full_revision(
        AnalysisRequest(
            ticker="6501.T",
            analysis_date=CUTOFF,
            analysts=("market",),
        ),
        _execution("6501.T"),
    )
    duplicate = baseline.current_state.claims[0].model_copy(
        update={"id": "claim_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
    )
    duplicate_coverage = baseline.coverage.claims[0].model_copy(update={"object_id": duplicate.id})
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"claims": (*baseline.current_state.claims, duplicate)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={"claims": (*baseline.coverage.claims, duplicate_coverage)}
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
    candidate_id = candidate.current_state.claims[0].id

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.claims[0].id == candidate_id
    assert updated.delta.claims[0].identity_disposition.value == "ambiguous_new"


def test_full_update_records_answered_question_as_material_change():
    baseline = assemble_full_revision(
        AnalysisRequest(ticker="6501.T", analysis_date=CUTOFF, analysts=("market",)),
        _execution("6501.T"),
    )
    baseline_question = ResearchQuestion(
        id="question_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        question="Will orders remain durable?",
        status=QuestionStatus.OPEN,
    )
    baseline = baseline.model_copy(
        update={
            "current_state": baseline.current_state.model_copy(
                update={"questions": (baseline_question,)}
            ),
            "coverage": baseline.coverage.model_copy(
                update={
                    "questions": (
                        ResearchObjectCoverage(
                            object_id=baseline_question.id,
                            status="limited",
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
    candidate_question = ResearchQuestion(
        id="question_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        question=baseline_question.question,
        status=QuestionStatus.ANSWERED,
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
                            status="complete",
                        ),
                    )
                }
            ),
        }
    )

    updated = assemble_full_update("revision-1", baseline, candidate)

    assert updated.current_state.questions[0].id == baseline_question.id
    assert updated.delta.questions[0].change.value == "answered"
    assert updated.outcome.value == "material_change"
