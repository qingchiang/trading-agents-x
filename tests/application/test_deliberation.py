from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.factories import analyst_report, research_case, research_decision
from tradingagents.application.contracts import (
    CalculationPurpose,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    EvidenceBundle,
    EvidenceItem,
    RebuttalReview,
    ValuationAssessment,
    ValuationRange,
)
from tradingagents.graph.deliberation import (
    debate_round_has_material_progress,
    invoke_research_case,
    invoke_research_decision,
    research_prompt,
)
from tradingagents.graph.structured_output import StructuredOutputError


class _StaticInvoker:
    def __init__(self, owner: _StaticLLM):
        self.owner = owner

    def invoke(self, prompt: str, config: Any = None) -> dict[str, Any]:
        del config
        self.owner.prompts.append(prompt)
        return {"raw": None, "parsed": self.owner.value}


class _StaticLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, value: Any):
        self.value = value
        self.prompts: list[str] = []

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> _StaticInvoker:
        return _StaticInvoker(self)


def _state(*, content: str = "Fixture evidence.") -> dict[str, Any]:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market snapshot",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content=content,
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    report = analyst_report(
        evidence_ref=item.ref,
        narrative="Complete analyst Markdown with a unique report marker.",
    )
    return {
        "ticker": "NVDA",
        "analysis_date": "2026-07-24",
        "output_language": "English (en)",
        "analyst_reports": {"market": report.model_dump(mode="json")},
        "evidence_bundle": bundle.model_dump(mode="json"),
        "cases": {},
        "rebuttals": [],
        "risk_reviews": {},
    }


def test_research_prompt_keeps_complete_reports_but_catalogs_long_evidence() -> None:
    evidence = "START-" + ("x" * 1_500) + "-TAIL-MARKER"
    state = _state(content=evidence)

    prompt = research_prompt(
        state,
        title="Fixture Role",
        objective="Inspect the full research record.",
        extra="No additional artifact.",
    )

    assert "Complete analyst Markdown with a unique report marker." in prompt
    assert "START-" not in prompt
    assert "-TAIL-MARKER" not in prompt
    assert '"content_characters": 1518' in prompt
    assert "EVIDENCE WORKSET" in prompt


def test_research_case_rejects_unknown_claim_after_bounded_recovery() -> None:
    state = _state()
    invalid = research_case(
        role="bull",
        evidence_ref=state["evidence_bundle"]["items"][0]["ref"],
        claim_id="market.claim_invented",
    )
    llm = _StaticLLM(invalid)

    with pytest.raises(StructuredOutputError) as error:
        invoke_research_case(
            llm,
            role="bull",
            prompt="Produce the bull case.",
            state=state,
            node="case.bull",
        )

    assert error.value.reason_code == "semantic_validation"
    assert len(llm.prompts) == 2


def test_debate_progress_requires_a_changed_open_issue_set() -> None:
    state = _state()
    agenda = DebateAgenda(
        summary="One material issue remains.",
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question="Will the mechanism persist?",
                importance=DebateImportance.MATERIAL,
            ),
            DebateIssue(
                id="debate.issue_2",
                question="Is valuation support durable?",
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    state["debate_agenda"] = agenda.model_dump(mode="json")
    first = RebuttalReview(
        role="bull",
        round=1,
        markdown="The operating issue remains open.",
        addressed_issue_ids=("debate.issue_1", "debate.issue_2"),
        open_issue_ids=("debate.issue_1",),
    )
    repeated = first.model_copy(update={"round": 2})
    changed = repeated.model_copy(
        update={"open_issue_ids": ("debate.issue_2",)}
    )

    state["rebuttals"] = [first.model_dump(mode="json")]
    assert debate_round_has_material_progress(state, round_number=1) is True

    state["rebuttals"].append(repeated.model_dump(mode="json"))
    assert debate_round_has_material_progress(state, round_number=2) is False

    state["rebuttals"][-1] = changed.model_dump(mode="json")
    assert debate_round_has_material_progress(state, round_number=2) is True


def test_final_decision_accepts_reproducible_critical_calculation() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "valuation_assessment": ValuationAssessment(
                method="Earnings multiple",
                valuation_range=ValuationRange(low=90, high=110),
                currency="USD",
                as_of_date=date(2026, 7, 24),
                input_evidence_refs=(ref,),
                limitations=("The multiple is scenario-dependent.",),
            ),
            "calculation_records": (
                CalculationRecord(
                    id="calc_valuation",
                    purpose=CalculationPurpose.VALUATION,
                    formula="earnings * multiple",
                    inputs={"earnings": 10, "multiple": 10},
                    input_evidence_refs=(ref,),
                    result=100,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    limitations=("The multiple is scenario-dependent.",),
                ),
            ),
        }
    )

    result = invoke_research_decision(
        _StaticLLM(decision),
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.calculation_records[0].result == 100


def test_final_decision_rejects_unreproducible_critical_calculation() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "calculation_records": (
                CalculationRecord(
                    id="calc_scenario",
                    purpose=CalculationPurpose.SCENARIO,
                    formula="base * growth",
                    inputs={"base": 100, "growth": 1.1},
                    input_evidence_refs=(ref,),
                    result=999,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    limitations=("Illustrative scenario only.",),
                ),
            ),
        }
    )
    llm = _StaticLLM(decision)

    with pytest.raises(StructuredOutputError):
        invoke_research_decision(
            llm,
            prompt="Form the final decision.",
            state=state,
            node="committee.final",
            require_risk_adjustments=False,
        )

    assert len(llm.prompts) == 2
