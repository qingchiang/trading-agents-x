from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.support.factories import research_decision
from tests.support.synthesis import (
    _core_draft_from_decision,
    _core_envelope,
    _MarkdownLLM,
    _numeric_regression_payload,
    _state,
    _state_with_agenda,
    _StaticLLM,
)
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    DebateImportance,
    NumericDisplayScale,
    ReportLanguage,
)
from tradingagents.domain.decision import (
    MarketReferenceLevel,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
    MeasurementKind,
)
from tradingagents.domain.numeric_audit import MarketReferenceBasis
from tradingagents.domain.reports import (
    DebateAgenda,
    DebateIssue,
    JudgeDraft,
    RebuttalReview,
    RiskReview,
)
from tradingagents.research.synthesis.decision_prompts import (
    _numeric_example_pair,
    decision_reference_label_guidance,
    decision_scenario_assumption_guidance,
)
from tradingagents.research.synthesis.deliberation import (
    debate_round_has_material_progress,
    invoke_debate_agenda,
    invoke_judge_draft,
    invoke_rebuttal,
    invoke_research_case,
    invoke_risk_review,
    write_research_markdown,
)
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericRequirementDraft,
)
from tradingagents.research.synthesis.numeric_evidence import build_numeric_value_catalog
from tradingagents.research.synthesis.numeric_generation import (
    _numeric_audit_snapshot,
)
from tradingagents.research.synthesis.numeric_math import (
    _canonicalize_calculation_result,
    _evaluate_formula,
)
from tradingagents.research.synthesis.numeric_preflight import (
    _preflight_numeric_requirements,
)
from tradingagents.research.synthesis.output_validation import OutputValidationError
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
    StructuredOutputFailure,
)


def test_research_markdown_uses_inline_ledger_refs_without_definitions() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    result = write_research_markdown(
        _MarkdownLLM(f"# Case\n\nSupported.[^{ref}]\n\n[^{ref}]: Model source text."),
        prompt="Write the case.",
        node="case.bull.write",
        allowed_evidence_refs=(ref,),
        output_language="English (en)",
        allow_continuation=True,
    )

    assert result.markdown == f"# Case\n\nSupported.[^{ref}]"
    assert result.warnings == ()


def test_research_markdown_can_normalize_truncated_output_without_continuation() -> None:
    class TruncatedMarkdownLLM:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, prompt: str, config: Any = None) -> Any:
            del prompt, config
            self.calls += 1
            return type(
                "Message",
                (),
                {
                    "content": "# Incremental update\n\nThe available evidence is limited.",
                    "response_metadata": {"finish_reason": "length"},
                },
            )()

    llm = TruncatedMarkdownLLM()
    result = write_research_markdown(
        llm,
        prompt="Write the update.",
        node="incremental.synthesis.semantic",
        allowed_evidence_refs=(),
        output_language="English (en)",
        allow_continuation=False,
    )

    assert llm.calls == 1
    assert result.markdown == "# Incremental update\n\nThe available evidence is limited."


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
    markdown = "## Constructive case\n\n| Measure | Reading |\n|---|---:|\n| Growth | 12.3% |\n"
    result = invoke_research_case(
        _StaticLLM(None),
        role="bull",
        markdown=markdown,
        state=state,
        node="case.bull.audit",
    )

    assert result.value.markdown == markdown


def test_agenda_audit_failure_uses_explicit_navigation_fallback() -> None:
    state = _state()
    llm = _StaticLLM({"summary": "", "issues": []})
    original_context = "FULL AGENDA CONTEXT MUST NOT BE REPEATED"
    result = invoke_debate_agenda(
        llm,
        prompt=original_context,
        state=state,
        node="debate.agenda.audit",
        output_language="English (en)",
    )

    assert result.value.issues[0].id == "debate.issue_audit_fallback"
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert "INVALID CANDIDATE JSON" in llm.prompts[1]
    assert original_context not in llm.prompts[1]


def test_agenda_prompt_and_fallback_follow_standard_output_language() -> None:
    state = _state()
    language = "Simplified Chinese (简体中文, zh-CN)"
    llm = _StaticLLM({"summary": "", "issues": []})

    result = invoke_debate_agenda(
        llm,
        prompt="已完成的主持人简报。",
        state=state,
        node="debate.agenda.audit",
        output_language=language,
    )

    assert "多空案例对一个重要经营机制存在分歧" in llm.prompts[0]
    assert all(language in prompt for prompt in llm.prompts)
    assert result.value.summary.startswith("已完成的多空案例")
    assert result.value.issues[0].question.endswith("是什么？")


def test_agenda_supports_reasoning_json_mode_transport() -> None:
    state = _state()
    agenda = DebateAgenda(
        summary="One material disagreement requires resolution.",
        issues=(
            DebateIssue(
                id="debate.issue_1",
                question="Will the operating improvement persist?",
                importance=DebateImportance.MATERIAL,
            ),
        ),
    )
    llm = _StaticLLM(agenda)
    llm.preferred_structured_output_method = "json_mode"

    result = invoke_debate_agenda(
        llm,
        prompt="Compare the completed bull and bear cases.",
        state=state,
        node="debate.agenda.serialize",
        output_language="English (en)",
    )

    assert result.value == agenda
    assert len(llm.prompts) == 1
    assert "Return exactly one JSON object" in llm.prompts[0]
    assert '"title": "DebateAgenda"' in llm.prompts[0]


def test_custom_language_agenda_failure_keeps_checkpoint_boundary() -> None:
    state = _state()
    custom_language = "Use formal Chinese and preserve Japanese legal names."

    with pytest.raises(StructuredOutputError):
        invoke_debate_agenda(
            _StaticLLM({"summary": "", "issues": []}),
            prompt="Completed moderator brief.",
            state=state,
            node="debate.agenda.audit",
            output_language=custom_language,
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
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert all("debate.issue_1" in prompt and "debate.issue_2" in prompt for prompt in llm.prompts)


def test_judge_audit_failure_preserves_markdown_without_fabricated_rating() -> None:
    state = _state_with_agenda()
    invalid = {
        "preliminary_rating": "Hold",
        "confidence": 0.5,
        "issue_dispositions": [{"issue_id": "debate.issue_invented", "status": "upheld"}],
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
    assert {item.issue_id: item.status for item in result.value.issue_dispositions} == {
        "debate.issue_1": "unresolved",
        "debate.issue_2": "unresolved",
    }
    assert result.generation_method is ArtifactGenerationMethod.MARKDOWN_AUDIT_INCOMPLETE
    assert all("debate.issue_1" in prompt and "debate.issue_2" in prompt for prompt in llm.prompts)


def test_risk_navigation_ignores_unknown_issue_ids_without_llm_audit() -> None:
    state = _state_with_agenda()
    llm = _StaticLLM(
        {
            "challenged_issue_ids": ["debate.issue_invented"],
            "unresolved_issue_ids": ["debate.issue_invented"],
        }
    )
    markdown = (
        "debate.issue_1 is challenged.\nUnresolved: debate.issue_2.\nIgnore debate.issue_invented."
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
    changed = repeated.model_copy(update={"open_issue_ids": ("debate.issue_2",)})

    state["rebuttals"] = [first.model_dump(mode="json")]
    assert debate_round_has_material_progress(state, round_number=1) is True

    state["rebuttals"].append(repeated.model_dump(mode="json"))
    assert debate_round_has_material_progress(state, round_number=2) is False

    state["rebuttals"][-1] = changed.model_dump(mode="json")
    assert debate_round_has_material_progress(state, round_number=2) is True


def test_numeric_prompt_example_pair_is_compatible_and_strictly_ordered() -> None:
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=tuple(
            EvidenceItem.create(
                source="fixture",
                evidence_type=label,
                requested_date=date(2026, 7, 24),
                effective_date=date(2026, 7, 24),
                value=value,
                measurement_kind=MeasurementKind.CURRENCY,
                unit="USD",
            )
            for label, value in (("upper", 120), ("lower", 80))
        ),
    )

    pair = _numeric_example_pair(build_numeric_value_catalog(bundle))

    assert pair is not None
    assert pair[0].value == 80
    assert pair[1].value == 120
    assert pair[0].measurement_kind is pair[1].measurement_kind
    assert pair[0].unit == pair[1].unit == "USD"


def test_public_decision_rejects_derived_reference_without_calculation() -> None:
    ref = _state()["evidence_bundle"]["items"][0]["ref"]

    with pytest.raises(ValueError, match="requires a calculation"):
        MarketReferenceLevel(
            label="Derived fair value",
            value=100,
            unit="USD",
            as_of_date=date(2026, 7, 24),
            interpretation="A derived reference only.",
            evidence_refs=(ref,),
            date_evidence_refs=(ref,),
            basis=MarketReferenceBasis.DERIVED,
        )


def test_display_scale_normalization_count_excludes_omitted_duplicate() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    retained = DecisionNumericRequirementDraft(
        id="req_duplicate_ratio",
        component_path="thesis",
        label="Retained ratio",
        stated_value=2,
        fraction_digits=1,
        formula="numerator / denominator",
        inputs=(
            CalculationInputDraft(name="numerator", value=2_000_000),
            CalculationInputDraft(name="denominator", value=1_000_000),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture limitation.",),
    )
    omitted_duplicate = retained.model_copy(
        update={
            "label": "Omitted duplicate ratio",
            "display_scale": NumericDisplayScale.MILLION,
        }
    )

    preflight = _preflight_numeric_requirements(
        _core_envelope(core, requirements=(retained, omitted_duplicate)),
        valid_evidence_refs={ref},
    )

    assert [item.id for item in preflight.requirements] == [retained.id]
    assert preflight.normalized_display_scales == 0
    assert preflight.omissions[0].issue_codes == ("numeric.requirement_candidate.1.duplicate_id",)


@pytest.mark.parametrize(
    ("output_language", "expected_label"),
    (
        (ReportLanguage.ENGLISH.prompt_label, "analyst target lower bound"),
        (ReportLanguage.SIMPLIFIED_CHINESE.prompt_label, "目标价下限"),
        (ReportLanguage.JAPANESE.prompt_label, "目標株価下限"),
    ),
)
def test_singleton_target_label_guidance_is_localized(
    output_language: str,
    expected_label: str,
) -> None:
    guidance = decision_reference_label_guidance(output_language)

    assert expected_label in guidance
    assert "two distinct" in guidance
    assert "Never duplicate one value ref" in guidance


def test_6501_scenario_assumption_regression_is_covered_by_prompt_guidance() -> None:
    regression = _numeric_regression_payload()["presentation_regressions"]["scenario_assumption"]
    guidance = decision_scenario_assumption_guidance(ReportLanguage.SIMPLIFIED_CHINESE.prompt_label)

    assert regression["expected"] in guidance
    assert regression["metric_subject"] in guidance
    assert f"不要只写‘{regression['ambiguous']}’" in guidance


def test_calculation_draft_exposes_identifier_inputs_in_json_schema() -> None:
    schema = CalculationRecordDraft.model_json_schema()
    input_schema = schema["$defs"]["CalculationInputDraft"]["properties"]

    assert input_schema["name"]["pattern"] == r"^[A-Za-z][A-Za-z0-9_]*$"
    assert schema["properties"]["inputs"]["items"] == {"$ref": "#/$defs/CalculationInputDraft"}


def test_calculation_draft_converts_typed_inputs_to_public_mapping() -> None:
    draft = CalculationRecordDraft(
        id="calc_valuation",
        formula="earnings * multiple",
        inputs=(
            CalculationInputDraft(name="earnings", value=10),
            CalculationInputDraft(name="multiple", value=10),
        ),
        input_evidence_refs=("ev_0123456789ab",),
        unit="USD",
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


@pytest.mark.parametrize(
    ("unit", "expected"),
    (
        ("%", 45.46),
        (" percent ", 45.46),
        ("pct", 45.46),
        ("pp", 45.46),
        ("percentage points", 45.46),
        ("bps", 4546),
        ("x", 0.4546),
        ("JPY", 0.4546),
    ),
)
def test_calculation_result_uses_unit_aware_canonical_values(
    unit: str,
    expected: float,
) -> None:
    assert _canonicalize_calculation_result(0.4546, unit) == pytest.approx(expected)


def test_numeric_audit_snapshot_redacts_secrets_and_omits_oversize_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tradingagents.research.synthesis.numeric_generation._NUMERIC_CANDIDATE_MAX_BYTES",
        32,
    )
    snapshot = _numeric_audit_snapshot(
        StructuredOutputFailure(
            phase="repair",
            method=ArtifactGenerationMethod.TOOL_CALL_RECOVERED,
            reason_code="semantic_validation",
            validation_issues=("semantic.numeric.appendix.invalid",),
            candidate={"api_key": "private", "payload": "x" * 100},
        )
    )

    assert snapshot.candidate is None
    assert snapshot.candidate_omitted == "oversize"
    assert snapshot.candidate_digest is not None


def test_numeric_audit_snapshot_keeps_only_sanitized_json_candidate() -> None:
    snapshot = _numeric_audit_snapshot(
        StructuredOutputFailure(
            phase="initial",
            method=ArtifactGenerationMethod.TOOL_CALL,
            reason_code="schema_validation",
            candidate={
                "token": "private",
                "requested": True,
                "note": "Authorization: Bearer-private",
            },
        )
    )

    assert snapshot.candidate == {
        "token": "[REDACTED]",
        "requested": True,
        "note": "Authorization: [REDACTED]",
    }
    assert snapshot.schema_valid is False
