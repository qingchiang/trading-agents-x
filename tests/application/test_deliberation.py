from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.factories import analyst_report, research_case
from tradingagents.application.contracts import (
    DebateAgenda,
    DebateImportance,
    EvidenceBundle,
    EvidenceItem,
    RebuttalOutcome,
    RebuttalPoint,
    RebuttalReview,
)
from tradingagents.graph.deliberation import (
    debate_round_has_material_progress,
    invoke_research_case,
    research_prompt,
)
from tradingagents.graph.structured_output import StructuredOutputError


class _StaticInvoker:
    def __init__(self, owner: _StaticLLM):
        self.owner = owner

    def invoke(self, prompt: str) -> dict[str, Any]:
        self.owner.prompts.append(prompt)
        return {"raw": None, "parsed": self.owner.value}


class _StaticLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, value: Any):
        self.value = value
        self.prompts: list[str] = []

    def with_structured_output(self, _schema, **_kwargs) -> _StaticInvoker:
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
        narrative="Complete analyst section with a unique report marker.",
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

    assert "Complete analyst section with a unique report marker." in prompt
    assert "START-" not in prompt
    assert "-TAIL-MARKER" not in prompt
    assert '"content_characters": 1518' in prompt
    assert "EVIDENCE CATALOG" in prompt


def test_research_case_rejects_unknown_claim_after_bounded_recovery() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    invalid = research_case(
        role="bull",
        evidence_ref=ref,
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


def test_debate_progress_requires_open_issue_and_new_material() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    state["debate_agenda"] = DebateAgenda(
        executive_summary="One material issue remains.",
        issues=(
            {
                "id": "debate.issue_1",
                "question": "Will the mechanism persist?",
                "claim_ids": ("market.claim_1",),
                "importance": DebateImportance.MATERIAL,
                "bull_position": "It persists.",
                "bear_position": "It fades.",
                "evidence_refs": (ref,),
            },
        ),
        evidence_refs=(ref,),
    ).model_dump(mode="json")
    first = RebuttalReview(
        role="bull",
        round=1,
        thesis_update="The case remains conditional.",
        responses=(
            RebuttalPoint(
                agenda_id="debate.issue_1",
                claim_ids=("market.claim_1",),
                response="The mechanism remains plausible.",
                causal_mechanism="A newly articulated demand transmission path.",
                outcome=RebuttalOutcome.UNRESOLVED,
                evidence_refs=(ref,),
            ),
        ),
        evidence_refs=(ref,),
    )
    repeated = first.model_copy(update={"round": 2})
    resolved = first.model_copy(
        update={
            "round": 2,
            "responses": (
                first.responses[0].model_copy(update={"outcome": RebuttalOutcome.UPHELD}),
            ),
        }
    )

    state["rebuttals"] = [first.model_dump(mode="json")]
    assert debate_round_has_material_progress(state, round_number=1) is True

    state["rebuttals"].append(repeated.model_dump(mode="json"))
    assert debate_round_has_material_progress(state, round_number=2) is False

    state["rebuttals"][-1] = resolved.model_dump(mode="json")
    assert debate_round_has_material_progress(state, round_number=2) is False
