"""Generate the qualitative decision and attach its audited numeric appendix."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from tradingagents.domain.common import (
    NumericDisplayScale,
    ReportLanguage,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
    RiskReviewDisposition,
)
from tradingagents.domain.decision import (
    ResearchDecision,
    ResearchScenario,
    RiskReviewAdjustment,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
)
from tradingagents.research.synthesis.decision_prompts import (
    _decision_example_text,
    _decision_language_rules,
    decision_display_scale_guidance,
    decision_operand_guidance,
    decision_percentage_calculation_guidance,
)
from tradingagents.research.synthesis.drafts import (
    CalculationInputDraft,
    DecisionNumericRequirementDraft,
    EventWriter,
    ResearchDecisionCoreEnvelope,
    ResearchDecisionOutput,
    ResearchScenarioCoreDraft,
)
from tradingagents.research.synthesis.numeric_audit import _apply_requirement_preflight
from tradingagents.research.synthesis.numeric_generation import _invoke_decision_numeric
from tradingagents.research.synthesis.numeric_preflight import _preflight_numeric_requirements
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputRunner,
)


def invoke_research_decision(
    llm: Any,
    *,
    numeric_llm: Any | None = None,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    require_risk_adjustments: bool,
    event_writer: EventWriter | None = None,
    output_language: str | None = None,
    metrics: Any | None = None,
) -> ResearchDecisionOutput:
    numeric_output_llm = llm if numeric_llm is None else numeric_llm
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    valid_refs = tuple(item.ref for item in bundle.items)
    first_ref = valid_refs[0]
    second_ref = valid_refs[1] if len(valid_refs) > 1 else first_ref
    example_input_refs = tuple(dict.fromkeys((first_ref, second_ref)))
    risk_roles = tuple(state.get("risk_reviews", {}))
    resolved_language = output_language or str(
        state.get("output_language") or ReportLanguage.ENGLISH.prompt_label
    )
    example_text = _decision_example_text(resolved_language)
    language_rules = _decision_language_rules(resolved_language)
    calculation_rules = decision_operand_guidance() + decision_percentage_calculation_guidance()
    display_scale_rules = decision_display_scale_guidance()
    example_adjustments = (
        (
            RiskReviewAdjustment(
                source_role=risk_roles[0],
                disposition=RiskReviewDisposition.MODIFIED,
                subject=example_text["adjustment_subject"],
                explanation=example_text["adjustment_explanation"],
                evidence_refs=(first_ref,),
            ),
        )
        if risk_roles
        else ()
    )

    def validate_core(
        result: ResearchDecisionCoreEnvelope,
    ) -> ResearchDecisionCoreEnvelope:
        scenario_kinds = tuple(item.kind for item in result.scenarios)
        if len(set(scenario_kinds)) != len(scenario_kinds):
            raise OutputValidationError("decision.scenarios.duplicate_kind")
        if set(scenario_kinds) != set(ResearchScenarioKind):
            raise OutputValidationError("decision.scenarios.incomplete_set")
        require_text(result.executive_summary)
        require_text(result.thesis)
        require_nonempty_texts(result.risks)
        require_nonempty_texts(result.invalidation_conditions)
        require_text(result.time_horizon)
        require_valid_refs(result.evidence_refs, set(valid_refs), required=True)
        for scenario in result.scenarios:
            require_nonempty_texts(scenario.core_assumptions)
            require_text(scenario.outcome)
            require_valid_refs(
                scenario.evidence_refs,
                set(valid_refs),
                required=True,
            )
        if require_risk_adjustments:
            adjusted_roles = {item.source_role for item in result.risk_review_adjustments}
            if not set(risk_roles).issubset(adjusted_roles):
                raise OutputValidationError("decision.risk_review.missing_role")
        if any(item.source_role not in risk_roles for item in result.risk_review_adjustments):
            raise OutputValidationError("decision.risk_review.unknown_role")
        for adjustment in result.risk_review_adjustments:
            require_text(adjustment.subject)
            require_text(adjustment.explanation)
            require_valid_refs(
                adjustment.evidence_refs,
                set(valid_refs),
                required=False,
            )
        return result

    core_example = ResearchDecisionCoreEnvelope(
        rating=ResearchRating.HOLD,
        confidence=ResearchConfidenceLevel.MEDIUM,
        executive_summary=example_text["executive_summary"],
        thesis=example_text["requirement_thesis"],
        evidence_refs=(first_ref,),
        risks=(example_text["risk"],),
        invalidation_conditions=(example_text["invalidation"],),
        unresolved_questions=(example_text["question"],),
        time_horizon=example_text["horizon"],
        scenarios=(
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BASE,
                core_assumptions=(example_text["base_assumption"],),
                outcome=example_text["base_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BULL,
                core_assumptions=(example_text["bull_assumption"],),
                outcome=example_text["bull_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioCoreDraft(
                kind=ResearchScenarioKind.BEAR,
                core_assumptions=(example_text["bear_assumption"],),
                outcome=example_text["bear_outcome"],
                evidence_refs=(first_ref,),
            ),
        ),
        risk_review_adjustments=example_adjustments,
        numeric_requirements_declared=True,
        numeric_requirement_candidates=(
            DecisionNumericRequirementDraft(
                id="req_example_percentage",
                component_path="thesis",
                label=example_text["requirement_label"],
                stated_value=45.5,
                fraction_digits=1,
                formula="(target_price - close_price) / close_price",
                inputs=(
                    CalculationInputDraft(
                        name="target_price",
                        value=145.5,
                        date_evidence_refs=(first_ref,),
                    ),
                    CalculationInputDraft(
                        name="close_price",
                        value=100,
                        date_evidence_refs=(second_ref,),
                    ),
                ),
                input_evidence_refs=example_input_refs,
                unit="%",
                display_scale=NumericDisplayScale.BASE,
                limitations=(example_text["valuation_limitation"],),
            ),
        ),
    )
    core_node = f"{node}.core"
    core_phase = (
        metrics.phase(core_node, event_writer=event_writer)
        if metrics is not None
        else nullcontext()
    )
    with core_phase:
        core = StructuredOutputRunner(
            llm=llm,
            schema=ResearchDecisionCoreEnvelope,
            validator=validate_core,
            node=core_node,
            event_writer=event_writer,
            repair_mode="preferred",
            include_candidate_in_repair=True,
            candidate_only_repair=True,
            invoke_config={"metadata": {"research_node": core_node}},
            repair_instructions=(
                "Keep valid research content. Use only allowed evidence refs. "
                "Classify final Decision confidence as low, medium, or high: low "
                "means the core judgment remains tentative because of material "
                "evidence gaps, conflicts, or unresolved assumptions; medium means "
                "the direction is supported with material uncertainty; high means "
                "reliable evidence strongly supports the core judgment with no "
                "unresolved material conflict. This is not a probability. "
                "Do not include valuation ranges, market-reference levels, "
                "or optional numeric components in this core object. Register every "
                "decision-critical derived exact number in "
                "numeric_requirement_candidates and set "
                "numeric_requirements_declared accordingly; "
                "directly observed Evidence values need no requirement. Candidate "
                "inputs must be an array of {name, value, date_evidence_refs} objects, "
                "never a dynamic mapping. Each observed input lists only the Evidence "
                "refs that establish its date; pure constants use an empty list. "
                "The union of inputs[*].date_evidence_refs must be a subset of the "
                "requirement's input_evidence_refs. Never place an Evidence ref only "
                "in date_evidence_refs. "
                "Limitations must be an array of strings. Every "
                "component_path must identify one exact core field such as risks.0 "
                "or catalysts.0 or scenarios.base.core_assumptions.2; omit an uncertain annotation "
                "instead of using a coarse path such as risks or scenarios. "
                "Do not create requirements for unresolved_questions. A displayed "
                "derived range requires two requirements with the same display_group_id "
                "and distinct range_low/range_high display_role values; never attach "
                "one scalar requirement to two calculations. "
                f"{calculation_rules} {display_scale_rules} "
                "scenarios must contain "
                "exactly one base, one bull, and one bear case. Required "
                f"risk-review roles: {json.dumps(risk_roles)}. {language_rules}"
            ),
        ).invoke(
            prompt + "\n\nSerialize only the strict qualitative decision core. "
            "Classify confidence as the rubric-based low, medium, or high research "
            "support level, never as a number or probability. Numeric valuation, "
            "scenario ranges, market reference levels, and canonical "
            "calculations are handled by a separate audit step. Preserve the brief's "
            "decision-critical calculation checklist as soft "
            "numeric_requirement_candidates. These annotations do not replace "
            "the strict qualitative fields. Candidate inputs are arrays of named "
            "values, limitations are string arrays, and component paths point to "
            "specific indexed core fields. Each formula input supplies "
            "date_evidence_refs for the Evidence that dates that input; explanatory "
            "background refs do not belong there. The union of "
            "inputs[*].date_evidence_refs must be a subset of the requirement's "
            "input_evidence_refs; never place an Evidence ref only in "
            "date_evidence_refs. Omit a candidate when its exact core location cannot "
            "be identified. Audit decision-critical derived values "
            "in catalysts, but do not annotate unresolved questions. Use paired "
            "range_low/range_high requirements with one display_group_id for a "
            "derived range. "
            + calculation_rules
            + " "
            + display_scale_rules
            + " "
            + language_rules
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(core_example.model_dump(mode="json"), ensure_ascii=False),
            example=core_example.model_dump(mode="json"),
            allowed_evidence_refs=valid_refs,
        )

    core_envelope = core.value
    core_value = core_envelope.qualitative_core()
    requirement_preflight = _preflight_numeric_requirements(
        core_envelope,
        valid_evidence_refs=set(valid_refs),
        event_writer=event_writer,
        node=f"{node}.numeric",
    )
    numeric_node = f"{node}.numeric"
    if event_writer is not None and requirement_preflight.normalized_display_scales:
        event_writer(
            {
                "event_type": "decision.numeric_display_scale_normalized",
                "node": numeric_node,
                "payload": {
                    "count": requirement_preflight.normalized_display_scales,
                },
            }
        )
    numeric_phase = (
        metrics.phase(numeric_node, event_writer=event_writer)
        if metrics is not None
        else nullcontext()
    )
    with numeric_phase:
        numeric = _invoke_decision_numeric(
            numeric_output_llm,
            prompt=prompt,
            node=numeric_node,
            bundle=bundle,
            allowed_evidence_refs=valid_refs,
            event_writer=event_writer,
            output_language=resolved_language,
            core_scenarios=core_value.scenarios,
            requirements=requirement_preflight.requirements,
        )
    numeric = _apply_requirement_preflight(
        numeric,
        requirement_preflight,
        node=numeric_node,
        event_writer=event_writer,
    )
    scenario_values = []
    for scenario in core_value.scenarios:
        numeric_scenarios = numeric.scenario_reference_ranges.get(scenario.kind, ())
        scenario_values.append(
            ResearchScenario(
                kind=scenario.kind,
                core_assumptions=scenario.core_assumptions,
                outcome=scenario.outcome,
                evidence_refs=scenario.evidence_refs,
                reference_ranges=numeric_scenarios,
            )
        )
    decision = ResearchDecision(
        rating=core_value.rating,
        confidence=core_value.confidence,
        executive_summary=core_value.executive_summary,
        thesis=core_value.thesis,
        evidence_refs=core_value.evidence_refs,
        catalysts=core_value.catalysts,
        risks=core_value.risks,
        invalidation_conditions=core_value.invalidation_conditions,
        unresolved_questions=core_value.unresolved_questions,
        time_horizon=core_value.time_horizon,
        scenarios=tuple(scenario_values),
        valuation_assessment=numeric.valuation_assessment,
        market_reference_levels=numeric.market_reference_levels,
        calculation_records=numeric.calculation_records,
        risk_review_adjustments=core_value.risk_review_adjustments,
        numeric_audit_status=numeric.status,
    )
    require_valid_refs(
        decision.evidence_refs,
        set(valid_refs),
        required=True,
    )
    return ResearchDecisionOutput(
        value=decision,
        generation_method=core.generation_method,
        numeric_generation_method=numeric.generation_method,
        warnings=numeric.warnings,
        numeric_audit=numeric.audit,
    )
