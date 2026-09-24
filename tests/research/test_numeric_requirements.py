from __future__ import annotations

import json
from typing import Any

import pytest

from tests.support.factories import research_decision
from tests.support.synthesis import (
    _core_draft_from_decision,
    _core_envelope,
    _numeric_3778_payload,
    _numeric_9984_payload,
    _SequenceLLM,
    _state,
    _value_catalog,
)
from tradingagents.domain.common import (
    NumericAuditAppendixStatus,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
)
from tradingagents.research.synthesis.decision import invoke_research_decision
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    ResearchDecisionCoreEnvelope,
)
from tradingagents.research.synthesis.numeric_audit import _assemble_numeric_draft
from tradingagents.research.synthesis.numeric_preflight import (
    _normalize_numeric_requirement_candidate,
    _preflight_numeric_requirements,
)
from tradingagents.research.synthesis.output_validation import OutputValidationError


def test_core_envelope_exposes_soft_numeric_requirement_schema() -> None:
    schema = ResearchDecisionCoreEnvelope.model_json_schema()
    candidates = schema["properties"]["numeric_requirement_candidates"]
    candidate = candidates["items"]

    assert candidate["type"] == "object"
    assert candidate["additionalProperties"] is False
    assert set(candidate["required"]) == {
        "id",
        "component_path",
        "label",
        "stated_value",
        "fraction_digits",
        "formula",
        "inputs",
        "input_evidence_refs",
        "unit",
        "display_scale",
        "limitations",
    }
    assert candidate["properties"]["inputs"]["type"] == "array"
    assert candidate["properties"]["inputs"]["items"] == {"$ref": "#/$defs/CalculationInputDraft"}
    component_pattern = candidate["properties"]["component_path"]["pattern"]
    assert "risks\\.\\d+" in component_pattern
    assert "catalysts\\.\\d+" in component_pattern
    assert "unresolved_questions" not in component_pattern
    assert "scenarios\\.(?:base|bull|bear)" in component_pattern
    display_scale_description = candidate["properties"]["display_scale"]["description"]
    assert "formula result only" in display_scale_description
    assert "Dimensionless" in display_scale_description


def test_core_declares_decision_critical_numeric_requirement() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core_draft = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    ).model_copy(update={"thesis": "The decision-critical earnings multiple is 82.1x."})
    requirement = DecisionNumericRequirementDraft(
        id="req_guidance_pe",
        component_path="thesis",
        label="Company-guidance forward PE",
        stated_value=82.1,
        fraction_digits=1,
        formula="price / eps",
        inputs=(
            CalculationInputDraft(name="price", value=3075),
            CalculationInputDraft(name="eps", value=37.46),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Guidance may change.",),
    )
    core = _core_envelope(core_draft, requirements=(requirement,))
    recovered_numeric = DecisionNumericDraft(
        requested=True,
        calculation_records=(
            CalculationRecordDraft(
                id="calc_guidance_pe",
                formula=requirement.formula,
                inputs=requirement.inputs,
                input_evidence_refs=requirement.input_evidence_refs,
                unit=requirement.unit,
                limitations=requirement.limitations,
                requirement_ids=(requirement.id,),
            ),
        ),
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [core],
            "DecisionNumericDraft": [
                DecisionNumericDraft(requested=False),
                recovered_numeric,
            ],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.thesis == core.thesis
    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert len(result.value.calculation_records) == 1
    assert result.value.calculation_records[0].result == pytest.approx(3075 / 37.46)
    assert result.value.calculation_records[0].decision_uses[0].component_path == "thesis"
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.RECOVERED
    assert '"numeric_requirement_candidates"' in llm.prompts[0][1]
    assert "decision-critical calculation checklist" in llm.prompts[0][1]
    assert "DECISION NUMERIC REQUIREMENTS" in llm.prompts[1][1]


def test_invalid_numeric_requirement_candidate_does_not_repair_core() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": "req_invalid",
                    "component_path": "thesis",
                    "label": "Incomplete candidate",
                }
            ],
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [envelope],
            "DecisionNumericDraft": [DecisionNumericDraft(requested=False)],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.thesis == core.thesis
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.numeric_audit is not None
    assert result.numeric_audit.snapshots == ()
    assert result.numeric_audit.omitted_components[0].component_path == "thesis"
    assert result.numeric_audit.omitted_components[0].issue_codes == (
        "numeric.requirement_candidate.0.stated_value.missing",
        "numeric.requirement_candidate.0.fraction_digits.missing",
        "numeric.requirement_candidate.0.formula.missing",
        "numeric.requirement_candidate.0.inputs.missing",
        "numeric.requirement_candidate.0.input_evidence_refs.missing",
        "numeric.requirement_candidate.0.unit.missing",
        "numeric.requirement_candidate.0.display_scale.missing",
        "numeric.requirement_candidate.0.limitations.missing",
    )
    assert result.numeric_audit.omitted_components[-1].issue_codes == (
        "numeric.requirements.declared_missing",
    )
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 1
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1


def test_numeric_requirement_preflight_reports_safe_field_paths() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": "req_invalid",
                    "component_path": "scenarios",
                    "label": "Invalid scenario calculation",
                    "stated_value": 1.0,
                    "fraction_digits": 9,
                    "formula": "value / divisor",
                    "inputs": {"value": 1.0, "divisor": 2.0},
                    "input_evidence_refs": [ref],
                    "unit": "x",
                    "display_text": "sensitive-candidate-value",
                }
            ],
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [envelope],
            "DecisionNumericDraft": [DecisionNumericDraft(requested=False)],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.numeric_audit is not None
    issue_codes = result.numeric_audit.omitted_components[0].issue_codes
    assert issue_codes == (
        "numeric.requirement_candidate.0.component_path.pattern",
        "numeric.requirement_candidate.0.fraction_digits.range",
        "numeric.requirement_candidate.0.inputs.list_type",
        "numeric.requirement_candidate.0.display_scale.missing",
        "numeric.requirement_candidate.0.limitations.missing",
        "numeric.requirement_candidate.0.extra.forbidden",
    )
    assert "sensitive-candidate-value" not in " ".join(issue_codes)
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 1


def test_numeric_requirement_preflight_canonicalizes_unicode_operands() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": "req_growth",
                    "component_path": "thesis",
                    "label": "Growth",
                    "stated_value": 25.0,
                    "fraction_digits": 1,
                    "formula": "(本期利润 - 上期利润) / 上期利润",
                    "inputs": [
                        {"name": "本期利润", "value": 125},
                        {"name": "上期利润", "value": 100},
                    ],
                    "input_evidence_refs": [ref],
                    "unit": "%",
                    "display_scale": "base",
                    "limitations": ["Fixture limitation."],
                }
            ],
        }
    )

    preflight = _preflight_numeric_requirements(
        envelope,
        valid_evidence_refs={ref},
    )

    assert preflight.issues == ()
    assert [item.name for item in preflight.requirements[0].inputs] == ["v1", "v2"]
    assert preflight.requirements[0].formula == "(v1 - v2) / v2"


@pytest.mark.parametrize(
    ("names", "formula"),
    (
        (("value", "2021"), "value / 2021"),
        (("value", "prior-year"), "value / prior-year"),
        (("Ａ", "A"), "Ａ / A"),
        (("value", "2021_NI"), "value / other_value"),
    ),
)
def test_numeric_requirement_preflight_does_not_guess_ambiguous_operands(
    names: tuple[str, str],
    formula: str,
) -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": "req_ambiguous",
                    "component_path": "thesis",
                    "label": "Ambiguous operands",
                    "stated_value": 1.0,
                    "fraction_digits": 1,
                    "formula": formula,
                    "inputs": [
                        {"name": names[0], "value": 2},
                        {"name": names[1], "value": 2},
                    ],
                    "input_evidence_refs": [ref],
                    "unit": "x",
                    "display_scale": "base",
                    "limitations": ["Fixture limitation."],
                }
            ],
        }
    )

    preflight = _preflight_numeric_requirements(
        envelope,
        valid_evidence_refs={ref},
    )

    assert preflight.requirements == ()
    assert any(issue.endswith(".name.pattern") for issue in preflight.issues)


def test_numeric_requirement_normalization_matches_overlapping_tokens_exactly() -> None:
    normalized = _normalize_numeric_requirement_candidate(
        {
            "formula": "2021_NI_adjusted - 2021_NI",
            "inputs": [
                {"name": "2021_NI_adjusted", "value": 120},
                {"name": "2021_NI", "value": 100},
            ],
        }
    )

    assert normalized["formula"] == "v1 - v2"
    assert [item["name"] for item in normalized["inputs"]] == ["v1", "v2"]


def test_4483_requirement_date_ref_mismatch_preserves_qualitative_decision() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    first_ref = bundle.items[0].ref
    other = EvidenceItem.create(
        source="fixture.other",
        evidence_type="market snapshot",
        requested_date=bundle.analysis_date,
        effective_date=bundle.analysis_date,
        value=2,
        unit="USD",
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=(*bundle.items, other),
        sealed_at=bundle.sealed_at,
    )
    state["evidence_bundle"] = bundle.model_dump(mode="json")
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(first_ref,)).model_dump(mode="json")
    )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                {
                    "id": "req_invalid_date_ref",
                    "component_path": "thesis",
                    "label": "Invalid date ref",
                    "stated_value": 50.0,
                    "fraction_digits": 1,
                    "formula": "value / divisor",
                    "inputs": [
                        {
                            "name": "value",
                            "value": 100,
                            "date_evidence_refs": [other.ref],
                        },
                        {"name": "divisor", "value": 2},
                    ],
                    "input_evidence_refs": [first_ref],
                    "unit": "x",
                    "display_scale": "base",
                    "limitations": ["Fixture limitation."],
                }
            ],
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [envelope],
            "DecisionNumericDraft": [DecisionNumericDraft(requested=False)],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.thesis == core.thesis
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.numeric_audit is not None
    assert result.numeric_audit.omitted_components[0].issue_codes == (
        "numeric.requirement_candidate.0.date_refs.not_input_refs",
    )
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1
    assert all(warning.code != "decision.numeric_repair_noop" for warning in result.warnings)
    core_prompt = next(
        prompt for schema, prompt in llm.prompts if schema == "ResearchDecisionCoreEnvelope"
    )
    assert "union of inputs[*].date_evidence_refs must be a subset" in core_prompt
    example_json = core_prompt.split("VALID EXAMPLE:\n", 1)[1].split(
        "\n\nALLOWED EVIDENCE REFS:", 1
    )[0]
    example = json.loads(example_json)
    example_requirement = example["numeric_requirement_candidates"][0]
    assert example_requirement["inputs"][0]["date_evidence_refs"] == [first_ref]
    assert example_requirement["inputs"][1]["date_evidence_refs"] == [other.ref]
    assert example_requirement["input_evidence_refs"] == [first_ref, other.ref]


def test_numeric_requirement_preflight_distinguishes_invalid_and_unbound_date_refs() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    first_ref = bundle.items[0].ref
    other = EvidenceItem.create(
        source="fixture.other",
        evidence_type="market snapshot",
        requested_date=bundle.analysis_date,
        effective_date=bundle.analysis_date,
        value=2,
        unit="USD",
    )
    bundle = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=(*bundle.items, other),
        sealed_at=bundle.sealed_at,
    )
    state["evidence_bundle"] = bundle.model_dump(mode="json")
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(first_ref,)).model_dump(mode="json")
    )
    unknown_ref = "ev_ffffffffffff"
    candidates = []
    for identifier, date_refs in (
        ("req_invalid_only", [unknown_ref]),
        ("req_invalid_and_unbound", [unknown_ref, other.ref]),
    ):
        candidates.append(
            {
                "id": identifier,
                "component_path": "thesis",
                "label": identifier,
                "stated_value": 50.0,
                "fraction_digits": 1,
                "formula": "value / divisor",
                "inputs": [
                    {
                        "name": "value",
                        "value": 100,
                        "date_evidence_refs": date_refs,
                    },
                    {"name": "divisor", "value": 2},
                ],
                "input_evidence_refs": [first_ref],
                "unit": "x",
                "display_scale": "base",
                "limitations": ["Fixture limitation."],
            }
        )
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": candidates,
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [envelope],
            "DecisionNumericDraft": [DecisionNumericDraft(requested=False)],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.thesis == core.thesis
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.numeric_audit is not None
    assert result.numeric_audit.omitted_components[0].issue_codes == (
        "numeric.requirement_candidate.0.date_refs.invalid_evidence",
    )
    assert result.numeric_audit.omitted_components[1].issue_codes == (
        "numeric.requirement_candidate.1.date_refs.invalid_evidence",
        "numeric.requirement_candidate.1.date_refs.not_input_refs",
    )
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 1
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1


def test_numeric_requirement_range_group_requires_low_and_high() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_range_low",
        component_path="scenarios.base.outcome",
        label="Range low",
        stated_value=10,
        fraction_digits=0,
        formula="earnings * multiple",
        inputs=(
            CalculationInputDraft(name="earnings", value=1),
            CalculationInputDraft(name="multiple", value=10),
        ),
        input_evidence_refs=(ref,),
        unit="USD",
        display_scale=NumericDisplayScale.BASE,
        display_role="range_low",
        display_group_id="group_base_range",
        limitations=("Fixture limitation.",),
    )
    envelope = _core_envelope(core, requirements=(requirement,))

    preflight = _preflight_numeric_requirements(
        envelope,
        valid_evidence_refs={ref},
    )

    assert preflight.requirements == ()
    assert preflight.issues == (
        "numeric.requirement_group.group_base_range.invalid",
        "numeric.requirements.declared_missing",
    )


def test_valid_numeric_requirements_survive_an_invalid_sibling() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    component_paths = (
        "executive_summary",
        "thesis",
        "risks.0",
        "invalidation_conditions.0",
        "scenarios.base.outcome",
        "scenarios.bull.core_assumptions.0",
    )
    requirements = tuple(
        DecisionNumericRequirementDraft(
            id=f"req_fixture_{index}",
            component_path=component_path,
            label=f"Fixture calculation {index}",
            stated_value=2.0,
            fraction_digits=1,
            formula=f"value_{index} / divisor_{index}",
            inputs=(
                CalculationInputDraft(name=f"value_{index}", value=4),
                CalculationInputDraft(name=f"divisor_{index}", value=2),
            ),
            input_evidence_refs=(ref,),
            unit="x",
            display_scale=NumericDisplayScale.BASE,
            limitations=("Fixture limitation.",),
        )
        for index, component_path in enumerate(component_paths)
    )
    invalid_candidate = {
        **requirements[0].model_dump(mode="json"),
        "id": "req_invalid_sibling",
        "component_path": "scenarios",
    }
    envelope = ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": True,
            "numeric_requirement_candidates": [
                *(item.model_dump(mode="json") for item in requirements),
                invalid_candidate,
            ],
        }
    )
    numeric = DecisionNumericDraft(
        requested=True,
        calculation_records=tuple(
            CalculationRecordDraft(
                id=f"calc_fixture_{index}",
                formula=requirement.formula,
                inputs=requirement.inputs,
                input_evidence_refs=requirement.input_evidence_refs,
                unit=requirement.unit,
                limitations=requirement.limitations,
                requirement_ids=(requirement.id,),
            )
            for index, requirement in enumerate(requirements)
        ),
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [envelope],
            "DecisionNumericDraft": [numeric],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert len(result.value.calculation_records) == len(requirements)
    assert {
        use.component_path
        for calculation in result.value.calculation_records
        for use in calculation.decision_uses
    } == set(component_paths)
    assert result.numeric_audit is not None
    assert len(result.numeric_audit.omitted_components) == 1
    assert result.numeric_audit.omitted_components[0].issue_codes == (
        "numeric.requirement_candidate.6.component_path.pattern",
    )
    assert not any(
        "declared_missing" in issue
        for omission in result.numeric_audit.omitted_components
        for issue in omission.issue_codes
    )
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 1
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1
    numeric_prompt = next(
        prompt for schema, prompt in llm.prompts if schema == "DecisionNumericDraft"
    )
    assert all(requirement.id in numeric_prompt for requirement in requirements)
    assert "req_invalid_sibling" not in numeric_prompt


def test_decision_requirements_use_decimal_rounding_and_publish_all_uses() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    regression = _numeric_3778_payload()["decision_audit_gap"]
    values = regression["values"]
    requirements = (
        DecisionNumericRequirementDraft(
            id="req_guidance_pe",
            component_path="thesis",
            label="Forward PE",
            stated_value=values["stated_forward_pe"],
            fraction_digits=1,
            formula="price / guidance_eps",
            inputs=(
                CalculationInputDraft(name="price", value=values["price"]),
                CalculationInputDraft(name="guidance_eps", value=values["company_guidance_eps"]),
            ),
            input_evidence_refs=(ref,),
            unit="x",
            display_scale=NumericDisplayScale.BASE,
            limitations=("Guidance may change.",),
        ),
        DecisionNumericRequirementDraft(
            id="req_eps_remaining",
            component_path="risks.0",
            label="Remaining EPS",
            stated_value=values["stated_remaining_eps"],
            fraction_digits=2,
            formula="guidance_eps - first_quarter_eps",
            inputs=(
                CalculationInputDraft(name="guidance_eps", value=values["company_guidance_eps"]),
                CalculationInputDraft(name="first_quarter_eps", value=values["first_quarter_eps"]),
            ),
            input_evidence_refs=(ref,),
            unit="JPY/share",
            display_scale=NumericDisplayScale.BASE,
            limitations=("Quarterly phasing may vary.",),
        ),
        DecisionNumericRequirementDraft(
            id="req_eps_run_rate",
            component_path="risk_review_adjustments.0.explanation",
            label="Required quarterly EPS",
            stated_value=values["stated_quarterly_run_rate"],
            fraction_digits=2,
            formula="remaining_eps / remaining_quarters",
            inputs=(
                CalculationInputDraft(name="remaining_eps", value=values["stated_remaining_eps"]),
                CalculationInputDraft(name="remaining_quarters", value=3),
            ),
            input_evidence_refs=(ref,),
            unit="JPY/share",
            display_scale=NumericDisplayScale.BASE,
            limitations=("Assumes even quarterly phasing.",),
        ),
    )
    draft = DecisionNumericDraft(
        requested=True,
        calculation_records=tuple(
            CalculationRecordDraft(
                id=f"calc_{requirement.id.removeprefix('req_')}",
                formula=requirement.formula,
                inputs=requirement.inputs,
                input_evidence_refs=requirement.input_evidence_refs,
                unit=requirement.unit,
                limitations=requirement.limitations,
                requirement_ids=(requirement.id,),
            )
            for requirement in requirements
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=requirements,
    )

    assert result.status is NumericAuditStatus.COMPLETE
    assert [item.result for item in result.calculation_records] == pytest.approx(
        [
            values["price"] / values["company_guidance_eps"],
            values["stated_remaining_eps"],
            values["stated_quarterly_run_rate"],
        ]
    )
    assert [item.decision_uses[0].component_path for item in result.calculation_records] == [
        "thesis",
        "risks.0",
        "risk_review_adjustments.0.explanation",
    ]


def test_decision_requirement_compares_canonical_amount_at_display_scale() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_capex",
        component_path="thesis",
        label="Capital expenditure",
        stated_value=805.98,
        fraction_digits=2,
        formula="first_half + second_half",
        inputs=(
            CalculationInputDraft(name="first_half", value=40_000_000_000),
            CalculationInputDraft(name="second_half", value=40_598_000_000),
        ),
        input_evidence_refs=(ref,),
        unit="USD",
        display_scale=NumericDisplayScale.HUNDRED_MILLION,
        limitations=("Fixture calculation.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_capex",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    result = _assemble_numeric_draft(
        DecisionNumericDraft(requested=True, calculation_records=(calculation,)),
        bundle=bundle,
        allowed_evidence_refs={ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    assert result.calculation_records[0].result == 80_598_000_000
    assert result.requirement_checks[0].comparison_result == 805.98
    assert result.requirement_checks[0].comparison_difference == 0
    assert result.requirement_checks[0].display_status is NumericDisplayStatus.MATCHED


@pytest.mark.parametrize(
    "unit",
    (
        "%",
        " percent ",
        "Pct",
        "PP",
        "percentage points",
        "bps",
        "Basis Points",
        "x",
        "倍",
    ),
)
def test_dimensionless_requirement_display_scale_is_normalized_to_base(
    unit: str,
) -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_dimensionless_scale",
        component_path="thesis",
        label="Dimensionless result",
        stated_value=2,
        fraction_digits=1,
        formula="numerator / denominator",
        inputs=(
            CalculationInputDraft(name="numerator", value=2_000_000),
            CalculationInputDraft(name="denominator", value=1_000_000),
        ),
        input_evidence_refs=(ref,),
        unit=unit,
        display_scale=NumericDisplayScale.MILLION,
        limitations=("Fixture limitation.",),
    )

    preflight = _preflight_numeric_requirements(
        _core_envelope(core, requirements=(requirement,)),
        valid_evidence_refs={ref},
    )

    assert preflight.issues == ()
    assert preflight.omissions == ()
    assert preflight.normalized_display_scales == 1
    assert preflight.requirements[0].display_scale is NumericDisplayScale.BASE


@pytest.mark.parametrize(
    ("unit", "display_scale"),
    (
        ("JPY", NumericDisplayScale.MILLION),
        ("USD", NumericDisplayScale.HUNDRED_MILLION),
        ("CNY", NumericDisplayScale.MILLION),
    ),
)
def test_amount_requirement_display_scale_is_preserved(
    unit: str,
    display_scale: NumericDisplayScale,
) -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_amount_scale",
        component_path="thesis",
        label="Amount result",
        stated_value=2,
        fraction_digits=1,
        formula="first + second",
        inputs=(
            CalculationInputDraft(name="first", value=1_000_000),
            CalculationInputDraft(name="second", value=1_000_000),
        ),
        input_evidence_refs=(ref,),
        unit=unit,
        display_scale=display_scale,
        limitations=("Fixture limitation.",),
    )

    preflight = _preflight_numeric_requirements(
        _core_envelope(core, requirements=(requirement,)),
        valid_evidence_refs={ref},
    )

    assert preflight.normalized_display_scales == 0
    assert preflight.requirements[0].display_scale is display_scale


def test_display_scale_normalization_count_excludes_omitted_requirements() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    valid = DecisionNumericRequirementDraft(
        id="req_valid_ratio",
        component_path="thesis",
        label="Valid ratio",
        stated_value=2,
        fraction_digits=1,
        formula="numerator / denominator",
        inputs=(
            CalculationInputDraft(name="numerator", value=2_000_000),
            CalculationInputDraft(name="denominator", value=1_000_000),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.MILLION,
        limitations=("Fixture limitation.",),
    )
    omitted = valid.model_copy(
        update={
            "id": "req_omitted_ratio",
            "component_path": "risks.99",
            "label": "Omitted ratio",
        }
    )

    preflight = _preflight_numeric_requirements(
        _core_envelope(core, requirements=(valid, omitted)),
        valid_evidence_refs={ref},
    )

    assert [item.id for item in preflight.requirements] == [valid.id]
    assert preflight.normalized_display_scales == 1
    assert preflight.omissions[0].issue_codes == (
        "numeric.requirement_candidate.1.unknown_component",
    )


def test_9984_percentage_requirements_complete_without_numeric_repair() -> None:
    payload = _numeric_9984_payload()
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirements = tuple(
        DecisionNumericRequirementDraft(
            id=item["id"],
            component_path=item["component_path"],
            label=item["label"],
            stated_value=item["stated_value"],
            fraction_digits=item["fraction_digits"],
            formula=item["formula"],
            inputs=tuple(
                CalculationInputDraft(name=name, value=value)
                for name, value in item["inputs"].items()
            ),
            input_evidence_refs=(ref,),
            unit=item["unit"],
            display_scale=NumericDisplayScale.BASE,
            limitations=("Live regression fixture limitation.",),
        )
        for item in payload["requirements"]
    )
    numeric = DecisionNumericDraft(
        requested=True,
        calculation_records=tuple(
            CalculationRecordDraft(
                id=f"calc_{requirement.id.removeprefix('req_')}",
                formula=requirement.formula,
                inputs=requirement.inputs,
                input_evidence_refs=requirement.input_evidence_refs,
                unit=requirement.unit,
                limitations=requirement.limitations,
                requirement_ids=(requirement.id,),
            )
            for requirement in requirements
        ),
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [_core_envelope(core, requirements=requirements)],
            "DecisionNumericDraft": [numeric],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.COMPLETE
    assert len(result.numeric_audit.requirement_checks) == 5
    assert all(
        check.calculation_status is NumericCalculationStatus.VERIFIED
        and check.display_status is NumericDisplayStatus.MATCHED
        for check in result.numeric_audit.requirement_checks
    )
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1
    assert len(result.value.calculation_records) == 5
    expected = [item["expected_result"] for item in payload["requirements"]]
    assert [item.result for item in result.value.calculation_records] == pytest.approx(expected)
    assert all(item.decision_uses for item in result.value.calculation_records)
    serialized = json.loads(result.value.model_dump_json())
    assert [item["result"] for item in serialized["calculation_records"]] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("update", "expected_issue"),
    (
        ({"unit": "JPY"}, "numeric.requirement.req_guidance_pe.unit_mismatch"),
        (
            {"formula": "price * guidance_eps"},
            "numeric.requirement.req_guidance_pe.formula_mismatch",
        ),
    ),
)
def test_decision_requirement_mismatch_is_rejected(
    update: dict[str, Any],
    expected_issue: str,
) -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_guidance_pe",
        component_path="thesis",
        label="Forward PE",
        stated_value=82.1,
        fraction_digits=1,
        formula="price / guidance_eps",
        inputs=(
            CalculationInputDraft(name="price", value=3075),
            CalculationInputDraft(name="guidance_eps", value=37.46),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Guidance may change.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_guidance_pe",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    ).model_copy(update=update)

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            DecisionNumericDraft(requested=True, calculation_records=(calculation,)),
            bundle=bundle,
            allowed_evidence_refs={ref},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
            requirements=(requirement,),
        )

    assert expected_issue in error.value.issue_codes


def test_scalar_requirement_cannot_cover_multiple_calculations() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_scalar",
        component_path="thesis",
        label="Scalar value",
        stated_value=10,
        fraction_digits=0,
        formula="value",
        inputs=(CalculationInputDraft(name="value", value=10),),
        input_evidence_refs=(ref,),
        unit="USD",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture limitation.",),
    )
    calculations = tuple(
        CalculationRecordDraft(
            id=f"calc_scalar_{index}",
            formula=requirement.formula,
            inputs=requirement.inputs,
            input_evidence_refs=requirement.input_evidence_refs,
            unit=requirement.unit,
            limitations=requirement.limitations,
            requirement_ids=(requirement.id,),
        )
        for index in range(2)
    )

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            DecisionNumericDraft(requested=True, calculation_records=calculations),
            bundle=bundle,
            allowed_evidence_refs={ref},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
            requirements=(requirement,),
        )

    assert "numeric.requirement.req_scalar.multiple_calculations" in error.value.issue_codes


@pytest.mark.parametrize("oversize", [False, True])
def test_rejected_numeric_candidate_retains_bounded_redacted_operands(oversize: bool) -> None:
    from tradingagents.credentials import use_credentials

    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(research_decision(evidence_refs=(ref,)).model_dump(mode="json"))
    invalid_name = "cash-total" if not oversize else "cash-" + "x" * 9000
    candidate = {
        "id": "req_cash", "component_path": "thesis", "label": "Net cash",
        "stated_value": 1, "fraction_digits": 1, "formula": invalid_name + " - debt",
        "inputs": [{"name": invalid_name, "value": 2}, {"name": "debt", "value": 1}],
        "input_evidence_refs": [ref], "unit": "USD", "display_scale": "base",
        "limitations": ["must-not-retain-unrelated-prose"],
        "extra": "must-not-retain-unrelated-prose",
    }
    envelope = ResearchDecisionCoreEnvelope.model_validate({
        **core.model_dump(mode="json"), "numeric_requirements_declared": True,
        "numeric_requirement_candidates": [candidate],
    })
    llm = _SequenceLLM({"ResearchDecisionCoreEnvelope": [envelope],
                        "DecisionNumericDraft": [DecisionNumericDraft(requested=False)]})
    events = []
    with use_credentials({"fixture": "debt"}):
        result = invoke_research_decision(
            llm, prompt="Form the decision.", state=state, node="committee.final",
            require_risk_adjustments=False, event_writer=events.append,
        )
    diagnostics = [e["payload"] for e in events if e["event_type"] == "decision.numeric_candidate_rejected"]
    assert len(diagnostics) == 1
    record = diagnostics[0]
    assert record["candidate_index"] == 0
    assert any(issue.endswith(".name.pattern") for issue in record["validation_issues"])
    if oversize:
        assert record["original"]["candidate_omitted"] == "oversize"
        assert "candidate" not in record["original"]
    else:
        assert record["original"]["candidate"]["formula"] == "cash-total - [REDACTED]"
        assert record["original"]["candidate"]["inputs"][0]["name"] == "cash-total"
    assert "debt" not in json.dumps(diagnostics)
    assert "must-not-retain-unrelated-prose" not in json.dumps(diagnostics)
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL


@pytest.mark.parametrize("names", [("2026年现金", "2025年债务"), ("class", "debt")])
def test_decision_preserves_unambiguous_operands_through_numeric_audit(names) -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(research_decision(evidence_refs=(ref,)).model_dump(mode="json"))
    core = core.model_copy(update={"thesis": "Net cash is 25 USD."})
    candidate = {
        "id": "req_cash", "component_path": "thesis", "label": "Net cash",
        "stated_value": 25, "fraction_digits": 0, "formula": f"{names[0]} - {names[1]}",
        "inputs": [{"name": names[0], "value": 125}, {"name": names[1], "value": 100}],
        "input_evidence_refs": [ref], "unit": "USD", "display_scale": "base",
        "limitations": ["Fixture limitation."],
    }
    envelope = ResearchDecisionCoreEnvelope.model_validate({
        **core.model_dump(mode="json"), "numeric_requirements_declared": True,
        "numeric_requirement_candidates": [candidate],
    })
    numeric = DecisionNumericDraft(requested=True, calculation_records=(CalculationRecordDraft(
        id="calc_cash", formula="v1 - v2",
        inputs=(CalculationInputDraft(name="v1", value=125), CalculationInputDraft(name="v2", value=100)),
        input_evidence_refs=(ref,), unit="USD", limitations=("Fixture limitation.",),
        requirement_ids=("req_cash",),
    ),))
    llm = _SequenceLLM({"ResearchDecisionCoreEnvelope": [envelope], "DecisionNumericDraft": [numeric]})
    result = invoke_research_decision(llm, prompt="Form the decision.", state=state,
                                     node="committee.final", require_risk_adjustments=False)
    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert result.value.calculation_records[0].result == 25
    assert result.numeric_audit.omitted_components == ()
    assert '"formula": "v1 - v2"' in llm.prompts[1][1]


@pytest.mark.parametrize("numeric_literal", ["1e3", "0x10", "1_000", "2j", "٢٠٢٦"])
def test_numeric_literal_operand_is_not_reinterpreted_as_a_variable(numeric_literal) -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(research_decision(evidence_refs=(ref,)).model_dump(mode="json"))
    candidate = {
        "id": "req_ambiguous", "component_path": "thesis", "label": "Ambiguous input",
        "stated_value": 25, "fraction_digits": 0, "formula": f"{numeric_literal} - debt",
        "inputs": [{"name": numeric_literal, "value": 125}, {"name": "debt", "value": 100}],
        "input_evidence_refs": [ref], "unit": "USD", "display_scale": "base",
        "limitations": ["Fixture limitation."],
    }
    envelope = ResearchDecisionCoreEnvelope.model_validate({
        **core.model_dump(mode="json"), "numeric_requirements_declared": True,
        "numeric_requirement_candidates": [candidate],
    })
    llm = _SequenceLLM({"ResearchDecisionCoreEnvelope": [envelope],
                        "DecisionNumericDraft": [DecisionNumericDraft(requested=False)]})
    events = []
    result = invoke_research_decision(llm, prompt="Form the decision.", state=state,
        node="committee.final", require_risk_adjustments=False, event_writer=events.append)
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert any(issue.endswith(".name.pattern") for omission in result.numeric_audit.omitted_components
               for issue in omission.issue_codes)
    assert any(e["event_type"] == "decision.numeric_candidate_rejected" for e in events)
