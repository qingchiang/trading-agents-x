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
    ResearchClaim,
    ResearchOpinion,
    ResearchScenarioState,
    ScenarioLikelihood,
    assemble_full_revision,
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
