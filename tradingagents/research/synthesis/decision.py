"""Serialize the adopted research conclusion and its optional references."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from tradingagents.domain.common import (
    ReportLanguage,
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
    RiskReviewDisposition,
)
from tradingagents.domain.decision import (
    RiskReviewAdjustment,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
)
from tradingagents.research.synthesis.decision_prompts import (
    _decision_example_text,
    _decision_language_rules,
)
from tradingagents.research.synthesis.drafts import (
    EventWriter,
    ResearchDecisionDraft,
    ResearchDecisionOutput,
    ResearchScenarioDraft,
)
from tradingagents.research.synthesis.output_validation import (
    OutputValidationError,
    require_nonempty_texts,
    require_text,
    require_valid_refs,
)
from tradingagents.research.synthesis.references import REFERENCE_GUIDANCE, assemble_decision
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputRunner,
)


def invoke_research_decision(
    llm: Any,
    *,
    prompt: str,
    state: Mapping[str, Any],
    node: str,
    require_risk_adjustments: bool,
    event_writer: EventWriter | None = None,
    output_language: str | None = None,
    metrics: Any | None = None,
) -> ResearchDecisionOutput:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    valid_refs = tuple(item.ref for item in bundle.items)
    first_ref = valid_refs[0]
    risk_roles = tuple(state.get("risk_reviews", {}))
    resolved_language = output_language or str(
        state.get("output_language") or ReportLanguage.ENGLISH.prompt_label
    )
    example_text = _decision_example_text(resolved_language)
    language_rules = _decision_language_rules(resolved_language)
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
        result: ResearchDecisionDraft,
    ) -> ResearchDecisionDraft:
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

    core_example = ResearchDecisionDraft(
        rating=ResearchRating.HOLD,
        confidence=ResearchConfidenceLevel.MEDIUM,
        executive_summary=example_text["executive_summary"],
        thesis=example_text["thesis"],
        evidence_refs=(first_ref,),
        risks=(example_text["risk"],),
        invalidation_conditions=(example_text["invalidation"],),
        unresolved_questions=(example_text["question"],),
        time_horizon=example_text["horizon"],
        scenarios=(
            ResearchScenarioDraft(
                kind=ResearchScenarioKind.BASE,
                core_assumptions=(example_text["base_assumption"],),
                outcome=example_text["base_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioDraft(
                kind=ResearchScenarioKind.BULL,
                core_assumptions=(example_text["bull_assumption"],),
                outcome=example_text["bull_outcome"],
                evidence_refs=(first_ref,),
            ),
            ResearchScenarioDraft(
                kind=ResearchScenarioKind.BEAR,
                core_assumptions=(example_text["bear_assumption"],),
                outcome=example_text["bear_outcome"],
                evidence_refs=(first_ref,),
            ),
        ),
        risk_review_adjustments=example_adjustments,
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
            schema=ResearchDecisionDraft,
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
                f"{REFERENCE_GUIDANCE} "
                "scenarios must contain "
                "exactly one base, one bull, and one bear case. Required "
                f"risk-review roles: {json.dumps(risk_roles)}. {language_rules}"
            ),
        ).invoke(
            prompt
            + "\n\n"
            + REFERENCE_GUIDANCE
            + language_rules
            + "\n\nLOCALIZED VALID EXAMPLE:\n"
            + json.dumps(core_example.model_dump(mode="json"), ensure_ascii=False),
            example=core_example.model_dump(mode="json"),
            allowed_evidence_refs=valid_refs,
        )

    decision = assemble_decision(
        core.value,
        bundle=bundle,
        allowed_evidence_refs=set(valid_refs),
        node=core_node,
        event_writer=event_writer,
    )
    return ResearchDecisionOutput(value=decision, generation_method=core.generation_method)
