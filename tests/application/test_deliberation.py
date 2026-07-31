from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    CalculationPurpose,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    EvidenceBundle,
    EvidenceItem,
    JudgeDraft,
    RebuttalReview,
    RiskReview,
    ValuationAssessment,
    ValuationRange,
)
from tradingagents.graph.deliberation import (
    CalculationInputDraft,
    CalculationRecordDraft,
    _evaluate_formula,
    debate_round_has_material_progress,
    invoke_debate_agenda,
    invoke_judge_draft,
    invoke_rebuttal,
    invoke_research_case,
    invoke_research_decision,
    invoke_risk_review,
    write_research_markdown,
)
from tradingagents.graph.output_validation import OutputValidationError
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


class _MarkdownLLM:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, prompt: str, config: Any = None) -> Any:
        del prompt, config
        return type(
            "Message",
            (),
            {
                "content": self.content,
                "response_metadata": {"finish_reason": "stop"},
            },
        )()


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


def test_research_markdown_uses_inline_ledger_refs_without_definitions() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    result = write_research_markdown(
        _MarkdownLLM(
            f"# Case\n\nSupported.[^{ref}]\n\n[^{ref}]: Model source text."
        ),
        prompt="Write the case.",
        node="case.bull.write",
        allowed_evidence_refs=(ref,),
    )

    assert result.markdown == f"# Case\n\nSupported.[^{ref}]"
    assert result.warnings == ()


def test_research_case_preserves_readable_markdown_without_navigation_ids() -> None:
    state = _state()
    llm = _StaticLLM(None)
    markdown = "## Bull case\n\nEvidence supports the constructive view."
    result = invoke_research_case(
        llm,
        role="bull",
        markdown=markdown,
        state=state,
        node="case.bull",
    )

    assert result.value.model_dump() == {"role": "bull", "markdown": markdown}
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDITED
    assert llm.prompts == []


def test_research_case_audit_preserves_completed_markdown() -> None:
    state = _state()
    markdown = (
        "## Constructive case\n\n"
        "| Measure | Reading |\n|---|---:|\n| Growth | 12.3% |\n"
    )
    result = invoke_research_case(
        _StaticLLM(None),
        role="bull",
        markdown=markdown,
        state=state,
        node="case.bull.audit",
    )

    assert result.value.markdown == markdown


def _state_with_agenda() -> dict[str, Any]:
    state = _state()
    state["debate_agenda"] = DebateAgenda(
        summary="Two material issues.",
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question="Will growth persist?",
                importance=DebateImportance.MATERIAL,
            ),
            DebateIssue(
                id="debate.issue_2",
                question="Is valuation support durable?",
                importance=DebateImportance.MATERIAL,
            ),
        ),
    ).model_dump(mode="json")
    return state


def test_agenda_audit_failure_uses_explicit_navigation_fallback() -> None:
    state = _state()
    result = invoke_debate_agenda(
        _StaticLLM({"summary": "", "issues": []}),
        prompt="Completed moderator brief.",
        state=state,
        node="debate.agenda.audit",
    )

    assert result.value.issues[0].id == "debate.issue_audit_fallback"
    assert (
        result.generation_method
        is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    )


@pytest.mark.parametrize(
    ("conservative_open", "expected_open"),
    [
        (False, ()),
        (True, ("debate.issue_1", "debate.issue_2")),
    ],
)
def test_rebuttal_audit_failure_preserves_markdown_with_profile_fallback(
    conservative_open: bool,
    expected_open: tuple[str, ...],
) -> None:
    state = _state_with_agenda()
    invalid = {
        "addressed_issue_ids": ["debate.issue_invented"],
        "open_issue_ids": ["debate.issue_invented"],
    }
    markdown = "debate.issue_1 is addressed; the other issue is discussed."

    llm = _StaticLLM(invalid)
    result = invoke_rebuttal(
        llm,
        role="bear",
        round_number=1,
        markdown=markdown,
        state=state,
        node="rebuttal.bear.audit",
        conservative_open=conservative_open,
    )

    assert result.value.markdown == markdown
    assert result.value.addressed_issue_ids == ("debate.issue_1",)
    assert result.value.open_issue_ids == expected_open
    assert (
        result.generation_method
        is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    )
    assert all(
        "debate.issue_1" in prompt and "debate.issue_2" in prompt
        for prompt in llm.prompts
    )


def test_judge_audit_failure_preserves_markdown_without_fabricated_rating() -> None:
    state = _state_with_agenda()
    invalid = {
        "preliminary_rating": "Hold",
        "confidence": 0.5,
        "issue_dispositions": [
            {"issue_id": "debate.issue_invented", "status": "upheld"}
        ],
    }

    llm = _StaticLLM(invalid)
    result = invoke_judge_draft(
        llm,
        markdown="## Judgment\n\nBoth material questions remain unresolved.",
        state=state,
        node="judge.research.audit",
    )

    assert isinstance(result.value, JudgeDraft)
    assert result.value.preliminary_rating is None
    assert result.value.confidence is None
    assert {
        item.issue_id: item.status
        for item in result.value.issue_dispositions
    } == {
        "debate.issue_1": "unresolved",
        "debate.issue_2": "unresolved",
    }
    assert (
        result.generation_method
        is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    )
    assert all(
        "debate.issue_1" in prompt and "debate.issue_2" in prompt
        for prompt in llm.prompts
    )


def test_risk_navigation_ignores_unknown_issue_ids_without_llm_audit() -> None:
    state = _state_with_agenda()
    llm = _StaticLLM(
        {
            "challenged_issue_ids": ["debate.issue_invented"],
            "unresolved_issue_ids": ["debate.issue_invented"],
        }
    )
    markdown = (
        "debate.issue_1 is challenged.\n"
        "Unresolved: debate.issue_2.\n"
        "Ignore debate.issue_invented."
    )

    result = invoke_risk_review(
        llm,
        role="integrated",
        markdown=markdown,
        state=state,
        node="risk.review.audit",
    )

    assert isinstance(result.value, RiskReview)
    assert result.value.challenged_issue_ids == (
        "debate.issue_1",
        "debate.issue_2",
    )
    assert result.value.unresolved_issue_ids == ("debate.issue_2",)
    assert llm.prompts == []


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


def test_calculation_draft_exposes_identifier_inputs_in_json_schema() -> None:
    schema = CalculationRecordDraft.model_json_schema()
    input_schema = schema["$defs"]["CalculationInputDraft"]["properties"]

    assert input_schema["name"]["pattern"] == r"^[A-Za-z][A-Za-z0-9_]*$"
    assert schema["properties"]["inputs"]["items"] == {
        "$ref": "#/$defs/CalculationInputDraft"
    }


def test_calculation_draft_converts_typed_inputs_to_public_mapping() -> None:
    draft = CalculationRecordDraft(
        id="calc_valuation",
        purpose=CalculationPurpose.VALUATION,
        formula="earnings * multiple",
        inputs=(
            CalculationInputDraft(name="earnings", value=10),
            CalculationInputDraft(name="multiple", value=10),
        ),
        input_evidence_refs=("ev_0123456789ab",),
        result=100,
        unit="USD",
        as_of_date=date(2026, 7, 24),
        limitations=("The multiple is scenario-dependent.",),
    )

    assert draft.input_mapping() == {"earnings": 10, "multiple": 10}


@pytest.mark.parametrize(
    ("formula", "inputs", "issue"),
    (
        (
            "base * growth",
            {"base": 100},
            "numeric.calculation.calc_base.formula.missing_input",
        ),
        (
            "base",
            {"base": 100, "growth": 1.1},
            "numeric.calculation.calc_base.formula.unused_input",
        ),
    ),
)
def test_formula_validation_reports_component_scoped_input_issues(
    formula: str,
    inputs: dict[str, float],
    issue: str,
) -> None:
    with pytest.raises(OutputValidationError) as error:
        _evaluate_formula(
            formula,
            inputs,
            issue_prefix="numeric.calculation.calc_base",
        )

    assert error.value.issue_code == issue


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

    with pytest.raises(StructuredOutputError) as error:
        invoke_research_decision(
            llm,
            prompt="Form the final decision.",
            state=state,
            node="committee.final",
            require_risk_adjustments=False,
        )

    assert len(llm.prompts) == 2
    assert error.value.validation_issues == (
        "semantic.decision.calculation.result_mismatch",
    )


def test_final_decision_reports_stable_duplicate_scenario_issue() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,))
    duplicate = decision.model_copy(
        update={
            "scenarios": (
                decision.scenarios[0],
                decision.scenarios[0],
                decision.scenarios[2],
            )
        }
    )

    with pytest.raises(StructuredOutputError) as error:
        invoke_research_decision(
            _StaticLLM(duplicate),
            prompt="Form the final decision.",
            state=state,
            node="committee.final",
            require_risk_adjustments=False,
        )

    assert error.value.validation_issues == (
        "schema.root.decision_scenarios_duplicate_kind",
    )
