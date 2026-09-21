from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from tests.support.factories import research_decision
from tests.support.synthesis import (
    _core_draft_from_decision,
    _core_envelope,
    _numeric_noop_repair_candidate,
    _numeric_regression,
    _numeric_regression_payload,
    _SequenceLLM,
    _state,
    _StaticLLM,
)
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    NumericAuditAppendixStatus,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
    ResearchScenarioKind,
    ScenarioReferenceCategory,
)
from tradingagents.domain.decision import (
    AuditedRangeEndpoint,
    CalculationRecord,
    EvidenceValueLocator,
    MarketReferenceLevel,
    RiskReviewAdjustment,
    ScenarioReferenceRange,
    ValuationAssessment,
)
from tradingagents.domain.evidence import (
    MeasurementKind,
)
from tradingagents.domain.numeric_audit import MarketReferenceBasis
from tradingagents.research.metrics import MetricsCallback
from tradingagents.research.synthesis.decision import invoke_research_decision
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
)
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputError,
)


def test_final_decision_accepts_reproducible_critical_calculation() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "valuation_assessment": ValuationAssessment(
                method="Earnings multiple",
                low=AuditedRangeEndpoint(
                    value=100,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    calculation_id="calc_valuation",
                    as_of_date=date(2026, 7, 24),
                ),
                high=AuditedRangeEndpoint(
                    value=100,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    calculation_id="calc_valuation",
                    as_of_date=date(2026, 7, 24),
                ),
                measurement_kind=MeasurementKind.CURRENCY,
                unit="USD",
                limitations=("The multiple is scenario-dependent.",),
            ),
            "calculation_records": (
                CalculationRecord(
                    id="calc_valuation",
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
    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE


def test_final_decision_accepts_observed_reference_without_calculation() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "market_reference_levels": (
                MarketReferenceLevel(
                    label="Observed close",
                    value=100,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    interpretation="Observed reference only.",
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    basis=MarketReferenceBasis.OBSERVED,
                    source_locator=EvidenceValueLocator(evidence_ref=ref),
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

    assert result.value.market_reference_levels == decision.market_reference_levels
    assert result.value.calculation_records == ()
    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE


def test_numeric_prompt_distinguishes_observed_ranges_from_valuations() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    llm = _StaticLLM(research_decision(evidence_refs=(ref,)))

    invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    numeric_prompt = llm.prompts[1]
    assert "scenario_reference_ranges" in numeric_prompt
    assert "technical bands" in numeric_prompt
    assert "not valuations" in numeric_prompt
    assert '"basis": "observed"' in numeric_prompt
    assert '"basis": "derived"' in numeric_prompt
    assert "low strictly less than high" in numeric_prompt
    assert "single numeric level in market_reference_levels" in numeric_prompt
    assert "must omit dates, values, units, basis names" in numeric_prompt
    example = json.loads(numeric_prompt.rsplit("LOCALIZED VALID EXAMPLE:\n", 1)[1])
    assert example["scenario_reference_ranges"] == {
        "base": [],
        "bull": [],
        "bear": [],
    }


def test_numeric_serializer_can_use_separate_json_mode_reasoning_client() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,))
    core_llm = _StaticLLM(decision)
    numeric_llm = _StaticLLM(decision)
    numeric_llm.preferred_structured_output_method = "json_mode"

    result = invoke_research_decision(
        core_llm,
        numeric_llm=numeric_llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert result.value.rating is decision.rating
    assert result.numeric_generation_method is ArtifactGenerationMethod.JSON_MODE
    assert len(core_llm.prompts) == 1
    assert len(numeric_llm.prompts) == 1
    assert "Return exactly one JSON object" not in core_llm.prompts[0]
    assert "Return exactly one JSON object" in numeric_llm.prompts[0]
    assert '"title": "DecisionNumericDraft"' in numeric_llm.prompts[0]


def test_numeric_serializer_repairs_seven_invalid_input_names() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    invalid_records = [
        {
            "id": f"calc_valuation_{index}",
            "formula": "earnings * multiple",
            "inputs": [
                {"name": "盈利", "value": 10},
                {"name": "倍数", "value": 10},
            ],
            "input_evidence_refs": [ref],
            "unit": "USD",
            "limitations": ["Illustrative only."],
        }
        for index in range(7)
    ]
    invalid_numeric = {
        "requested": True,
        "scenario_reference_ranges": {"base": [], "bull": [], "bear": []},
        "valuation_assessment": None,
        "market_reference_levels": [],
        "calculation_records": invalid_records,
    }
    recovered_numeric = DecisionNumericDraft(
        requested=False,
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [_core_envelope(core)],
            "DecisionNumericDraft": [invalid_numeric, recovered_numeric],
        }
    )
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        event_writer=events.append,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.NOT_APPLICABLE
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.RECOVERED
    assert result.numeric_generation_method is ArtifactGenerationMethod.TOOL_CALL_RECOVERED
    assert [item.phase.value for item in result.numeric_audit.snapshots] == ["initial"]
    assert result.numeric_audit.snapshots[0].candidate == invalid_numeric
    assert [event["event_type"] for event in events] == [
        "node.numeric_audit_retry",
        "node.numeric_audit_recovered",
    ]
    issues = events[0]["payload"]["validation_issues"]
    assert len(issues) > 8
    assert issues[0].startswith("schema.calculation_records.0.inputs")
    assert any(issue.startswith("schema.calculation_records.6.inputs") for issue in issues)


def test_unknown_risk_adjustment_evidence_remains_a_core_failure() -> None:
    state = _state()
    state["risk_reviews"] = {"integrated": {}}
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    invalid = core.model_copy(
        update={
            "risk_review_adjustments": (
                RiskReviewAdjustment(
                    source_role="integrated",
                    disposition="modified",
                    subject="Risk calibration",
                    explanation="The risk review changed confidence.",
                    evidence_refs=("ev_deadbeefdead",),
                ),
            )
        }
    )
    repaired = core.model_copy(
        update={
            "risk_review_adjustments": (
                RiskReviewAdjustment(
                    source_role="integrated",
                    disposition="modified",
                    subject="Risk calibration",
                    explanation="The risk review changed confidence.",
                    evidence_refs=(ref,),
                ),
            )
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [
                _core_envelope(invalid),
                _core_envelope(repaired),
            ],
            "DecisionNumericDraft": [DecisionNumericDraft(requested=False)],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=True,
    )

    assert result.value.risk_review_adjustments[0].evidence_refs == (ref,)
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 2


def test_missing_decision_calculation_degrades_numeric_audit_only_once() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core_draft = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    ).model_copy(update={"thesis": "The remaining quarterly EPS is 16.08."})
    requirement = DecisionNumericRequirementDraft(
        id="req_eps_remaining",
        component_path="thesis",
        label="Remaining EPS guidance",
        stated_value=16.08,
        fraction_digits=2,
        formula="guidance_eps - first_quarter_eps",
        inputs=(
            CalculationInputDraft(name="guidance_eps", value=37.46),
            CalculationInputDraft(name="first_quarter_eps", value=21.38),
        ),
        input_evidence_refs=(ref,),
        unit="JPY/share",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Quarterly phasing may vary.",),
    )
    core = _core_envelope(core_draft, requirements=(requirement,))
    empty_numeric = DecisionNumericDraft(requested=False)
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [core],
            "DecisionNumericDraft": [empty_numeric, empty_numeric],
        }
    )
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        event_writer=events.append,
    )

    assert result.value.thesis == core.thesis
    assert result.value.calculation_records == ()
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.PARTIAL
    assert result.numeric_audit.omitted_components[0].component_path == "thesis"
    assert result.numeric_audit.omitted_components[0].issue_codes == (
        "numeric.requirement.req_eps_remaining.missing_calculation",
    )
    assert [event["event_type"] for event in events] == [
        "node.numeric_audit_retry",
        "node.numeric_audit_degraded",
    ]
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 2


def test_7011_dimensionless_scales_normalize_without_numeric_retry() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirements = (
        DecisionNumericRequirementDraft(
            id="req_fy2026_net_income_growth",
            component_path="executive_summary",
            label="FY2026 net income growth",
            stated_value=35.3,
            fraction_digits=1,
            formula=("(fy2026_net_income - fy2025_net_income) / fy2025_net_income"),
            inputs=(
                CalculationInputDraft(name="fy2026_net_income", value=332_129),
                CalculationInputDraft(name="fy2025_net_income", value=245_447),
            ),
            input_evidence_refs=(ref,),
            unit="%",
            display_scale=NumericDisplayScale.MILLION,
            limitations=("Inputs are reported in million JPY.",),
        ),
        DecisionNumericRequirementDraft(
            id="req_fy2026_ocf_net_income_ratio",
            component_path="thesis",
            label="FY2026 operating cash flow to net income",
            stated_value=2.84,
            fraction_digits=2,
            formula="fy2026_ocf / fy2026_net_income",
            inputs=(
                CalculationInputDraft(name="fy2026_ocf", value=942_619),
                CalculationInputDraft(name="fy2026_net_income", value=332_129),
            ),
            input_evidence_refs=(ref,),
            unit="倍",
            display_scale=NumericDisplayScale.MILLION,
            limitations=("Inputs are reported in million JPY.",),
        ),
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
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        event_writer=events.append,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.COMPLETE
    assert all(
        check.calculation_status is NumericCalculationStatus.VERIFIED
        and check.display_status is NumericDisplayStatus.MATCHED
        and check.display_scale is NumericDisplayScale.BASE
        for check in result.numeric_audit.requirement_checks
    )
    assert events == [
        {
            "event_type": "decision.numeric_display_scale_normalized",
            "node": "committee.final.numeric",
            "payload": {"count": 2},
        }
    ]
    assert [schema for schema, _prompt in llm.prompts].count("ResearchDecisionCoreEnvelope") == 1
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1


def test_display_mismatch_does_not_retry_numeric_serializer() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_guidance_pe",
        component_path="thesis",
        label="Forward PE",
        stated_value=45.8,
        fraction_digits=1,
        formula="price / guidance_eps",
        inputs=(
            CalculationInputDraft(name="price", value=3834.343755),
            CalculationInputDraft(name="guidance_eps", value=1),
        ),
        input_evidence_refs=(ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Guidance may change.",),
    )
    numeric = DecisionNumericDraft(
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
            "ResearchDecisionCoreEnvelope": [_core_envelope(core, requirements=(requirement,))],
            "DecisionNumericDraft": [numeric],
        }
    )
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        event_writer=events.append,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert len(result.value.calculation_records) == 1
    assert [schema for schema, _prompt in llm.prompts].count("DecisionNumericDraft") == 1
    numeric_events = [
        event["event_type"]
        for event in events
        if event["event_type"].startswith("node.numeric_audit")
    ]
    assert numeric_events == ["node.numeric_audit_degraded"]


@pytest.mark.parametrize(
    ("output_language", "localized_example", "assumption_example"),
    (
        (
            "Simplified Chinese (简体中文, zh-CN)",
            "现有证据支持一项平衡的研究结论",
            "分析师 EPS 共识上修至每股 185–195 日元",
        ),
        (
            "使用正式、克制的繁体中文",
            "The evidence supports a balanced conclusion",
            "Analyst EPS consensus rises to JPY 185-195 per share",
        ),
    ),
)
def test_final_serializers_preserve_output_language_in_primary_and_repair(
    output_language: str,
    localized_example: str,
    assumption_example: str,
) -> None:
    state = _state()
    state["output_language"] = output_language
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    )
    invalid_core = core.model_dump(mode="json")
    invalid_core["thesis"] = ""
    invalid_numeric = {
        "requested": True,
        "scenario_reference_ranges": {"base": [], "bull": [], "bear": []},
        "valuation_assessment": None,
        "market_reference_levels": [],
        "calculation_records": [],
    }
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [invalid_core, _core_envelope(core)],
            "DecisionNumericDraft": [invalid_numeric, DecisionNumericDraft(requested=False)],
        }
    )

    invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
        output_language=output_language,
    )

    assert len(llm.prompts) == 4
    assert all(output_language in prompt for _schema, prompt in llm.prompts)
    assert localized_example in llm.prompts[0][1]
    assert localized_example in llm.prompts[1][1]
    assert all(assumption_example in prompt for _schema, prompt in llm.prompts[:2])
    assert all(
        "formulas must return a fractional ratio" in prompt for _schema, prompt in llm.prompts
    )
    assert all(
        "union of inputs[*].date_evidence_refs must be a subset" in prompt
        for _schema, prompt in llm.prompts[:2]
    )
    assert all(
        "must never be inherited from an input's measurement scale" in prompt
        for _schema, prompt in llm.prompts
    )
    assert all("display_scale=base, not million" in prompt for _schema, prompt in llm.prompts)


def test_final_serializer_phases_record_child_wall_time_without_parent_span() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    metrics = MetricsCallback()
    events: list[dict[str, Any]] = []

    invoke_research_decision(
        _StaticLLM(research_decision(evidence_refs=(ref,))),
        prompt="Form the final decision.",
        state=state,
        node="committee.final.serialize",
        require_risk_adjustments=False,
        event_writer=events.append,
        metrics=metrics,
    )

    snapshot = metrics.snapshot()
    assert "committee.final.serialize" not in snapshot.node_metrics
    assert snapshot.node_metrics["committee.final.serialize.core"].wall_time_seconds >= 0
    assert snapshot.node_metrics["committee.final.serialize.numeric"].wall_time_seconds >= 0
    assert [
        (event["event_type"], event["node"])
        for event in events
        if event["event_type"].startswith("phase.")
    ] == [
        ("phase.started", "committee.final.serialize.core"),
        ("phase.completed", "committee.final.serialize.core"),
        ("phase.started", "committee.final.serialize.numeric"),
        ("phase.completed", "committee.final.serialize.numeric"),
    ]


def test_numeric_serializer_receives_validated_core_scenario_catalog() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,))
    llm = _StaticLLM(decision)

    invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    numeric_prompt = llm.prompts[1]
    assert "SCENARIO CATALOG" in numeric_prompt
    for scenario in decision.scenarios:
        assert scenario.outcome in numeric_prompt
        assert scenario.core_assumptions[0] in numeric_prompt


def test_repeated_cross_scenario_repair_preserves_other_numeric_components() -> None:
    payload = _numeric_regression_payload()
    case = payload["scenario_alignment_cases"][0]
    bundle, draft = _numeric_regression()
    valid_range = draft.scenario_reference_ranges.base[0]
    mismatched_range = valid_range.model_copy(update={"label": case["label"]})
    invalid_numeric = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (valid_range, mismatched_range)}
            )
        }
    )
    state = _state()
    state["evidence_bundle"] = bundle.model_dump(mode="json")
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(bundle.items[0].ref,)).model_dump(mode="json")
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [_core_envelope(core)],
            "DecisionNumericDraft": [invalid_numeric, invalid_numeric],
        }
    )
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final.serialize",
        require_risk_adjustments=False,
        output_language=case["output_language"],
        event_writer=events.append,
    )

    base = next(
        scenario
        for scenario in result.value.scenarios
        if scenario.kind is ResearchScenarioKind.BASE
    )
    assert [item.label for item in base.reference_ranges] == [valid_range.label]
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.value.market_reference_levels
    assert result.value.calculation_records
    assert result.numeric_audit is not None
    assert {item.component_path for item in result.numeric_audit.omitted_components} == {
        "numeric.scenario.base.ranges.1"
    }
    assert [event["event_type"] for event in events] == [
        "node.numeric_audit_retry",
        "node.numeric_audit_degraded",
    ]


def test_6501_invalid_numeric_tool_candidate_is_repaired_and_retained() -> None:
    bundle, valid_numeric = _numeric_regression()
    state = _state()
    state["evidence_bundle"] = bundle.model_dump(mode="json")
    state["output_language"] = "Simplified Chinese (简体中文, zh-CN)"
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(bundle.items[0].ref,)).model_dump(mode="json")
    )
    invalid_candidate = valid_numeric.model_dump(mode="json")
    invalid_candidate["calculation_records"][3]["limitations"] = []
    parsing_error = None
    try:
        DecisionNumericDraft.model_validate(invalid_candidate)
    except Exception as exc:  # Pydantic detail is intentionally not persisted.
        parsing_error = exc
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [_core_envelope(core)],
            "DecisionNumericDraft": [
                {
                    "raw": AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "DecisionNumericDraft",
                                "args": invalid_candidate,
                                "id": "call_numeric",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    "parsed": None,
                    "parsing_error": parsing_error,
                },
                valid_numeric,
            ],
        }
    )

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final.serialize",
        require_risk_adjustments=False,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.RECOVERED
    assert result.numeric_audit.snapshots[0].candidate == invalid_candidate
    assert result.numeric_audit.snapshots[0].candidate_digest


def test_identical_failed_numeric_repair_is_degraded_not_recovered() -> None:
    bundle, _ = _numeric_regression()
    state = _state()
    state["evidence_bundle"] = bundle.model_dump(mode="json")
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(bundle.items[0].ref,)).model_dump(mode="json")
    )
    invalid_numeric = _numeric_noop_repair_candidate()
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [_core_envelope(core)],
            "DecisionNumericDraft": [invalid_numeric, invalid_numeric],
        }
    )
    events: list[dict[str, Any]] = []

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final.serialize",
        require_risk_adjustments=False,
        event_writer=events.append,
    )

    assert result.value.numeric_audit_status is NumericAuditStatus.INCOMPLETE
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.INCOMPLETE
    assert result.numeric_generation_method is ArtifactGenerationMethod.TOOL_CALL_RECOVERED
    assert len(result.numeric_audit.snapshots) == 2
    assert (
        result.numeric_audit.snapshots[0].candidate_digest
        == result.numeric_audit.snapshots[1].candidate_digest
    )
    assert [event["event_type"] for event in events] == [
        "node.numeric_audit_retry",
        "node.numeric_audit_degraded",
    ]
    assert any(warning.code == "decision.numeric_repair_noop" for warning in result.warnings)


def test_final_decision_recomputes_optional_calculation_result() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "scenarios": tuple(
                scenario.model_copy(
                    update={
                        "reference_ranges": (
                            ScenarioReferenceRange(
                                category=ScenarioReferenceCategory.FUNDAMENTAL,
                                label="Derived scenario reference",
                                low=AuditedRangeEndpoint(
                                    value=100,
                                    basis=MarketReferenceBasis.DERIVED,
                                    evidence_refs=(ref,),
                                    date_evidence_refs=(ref,),
                                    calculation_id="calc_scenario_low",
                                    as_of_date=date(2026, 7, 24),
                                ),
                                high=AuditedRangeEndpoint(
                                    value=999,
                                    basis=MarketReferenceBasis.DERIVED,
                                    evidence_refs=(ref,),
                                    date_evidence_refs=(ref,),
                                    calculation_id="calc_scenario_high",
                                    as_of_date=date(2026, 7, 24),
                                ),
                                unit="USD",
                                interpretation="Illustrative derived range.",
                                limitations=("Illustrative scenario only.",),
                            ),
                        ),
                    }
                )
                if scenario.kind.value == "base"
                else scenario
                for scenario in research_decision(evidence_refs=(ref,)).scenarios
            ),
            "calculation_records": (
                CalculationRecord(
                    id="calc_scenario_low",
                    formula="base * floor_multiple",
                    inputs={"base": 100, "floor_multiple": 1},
                    input_evidence_refs=(ref,),
                    result=999,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    limitations=("Illustrative scenario only.",),
                ),
                CalculationRecord(
                    id="calc_scenario_high",
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

    result = invoke_research_decision(
        llm,
        prompt="Form the final decision.",
        state=state,
        node="committee.final",
        require_risk_adjustments=False,
    )

    assert len(llm.prompts) == 2
    assert [item.result for item in result.value.calculation_records] == pytest.approx(
        [100.0, 110.0]
    )
    assert result.value.numeric_audit_status is NumericAuditStatus.COMPLETE
    assert result.numeric_audit is None


def test_final_decision_preserves_valid_numeric_components_after_repair_failure() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    decision = research_decision(evidence_refs=(ref,)).model_copy(
        update={
            "valuation_assessment": ValuationAssessment(
                method="Earnings multiple",
                low=AuditedRangeEndpoint(
                    value=100,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    calculation_id="calc_valuation",
                    as_of_date=date(2026, 7, 24),
                ),
                high=AuditedRangeEndpoint(
                    value=100,
                    basis=MarketReferenceBasis.DERIVED,
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    calculation_id="calc_valuation",
                    as_of_date=date(2026, 7, 24),
                ),
                measurement_kind=MeasurementKind.CURRENCY,
                unit="USD",
                limitations=("The multiple is scenario-dependent.",),
            ),
            "market_reference_levels": (
                MarketReferenceLevel(
                    label="Observed close",
                    value=100,
                    unit="USD",
                    as_of_date=date(2026, 7, 24),
                    interpretation="Observed reference only.",
                    evidence_refs=(ref,),
                    date_evidence_refs=(ref,),
                    basis=MarketReferenceBasis.OBSERVED,
                    source_locator=EvidenceValueLocator(evidence_ref=ref),
                ),
            ),
            "calculation_records": (
                CalculationRecord(
                    id="calc_valuation",
                    formula="earnings * missing_multiple",
                    inputs={"earnings": 10, "multiple": 10},
                    input_evidence_refs=(ref,),
                    result=999,
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

    assert result.value.valuation_assessment is None
    assert result.value.market_reference_levels == decision.market_reference_levels
    assert result.value.calculation_records == ()
    assert result.value.numeric_audit_status is NumericAuditStatus.PARTIAL
    assert result.warnings[0].code == "decision.numeric_audit_partial"
    assert result.numeric_audit is not None
    assert result.numeric_audit.status is NumericAuditAppendixStatus.PARTIAL
    assert {item.component_path for item in result.numeric_audit.omitted_components} == {
        "numeric.calculation.calc_valuation",
        "numeric.valuation",
    }


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

    assert error.value.validation_issues == ("semantic.decision.scenarios.duplicate_kind",)
