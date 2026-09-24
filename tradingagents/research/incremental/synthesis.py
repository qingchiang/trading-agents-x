"""Incremental semantic analysis, bounded serialization, and recovery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.configuration.settings import RunSettings
from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    ReportLanguage,
    report_language_prompt_label,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.decision_components import baseline_component_ids
from tradingagents.domain.incremental import (
    FullResearchRequiredReason,
    IncrementalAnalysisBrief,
    IncrementalDecisionOutcome,
    IncrementalSynthesis,
    IncrementalSynthesisInput,
    ReassessmentDisposition,
    ResearchReassessment,
)
from tradingagents.llm.runtime import RunLLMs
from tradingagents.research.incremental.context import incremental_prompt_input
from tradingagents.research.metrics import MetricsCallback
from tradingagents.research.presentation import parse_markdown_sections
from tradingagents.research.synthesis.deliberation import write_research_markdown
from tradingagents.research.synthesis.drafts import (
    ResearchDecisionCoreDraft,
    ResearchDecisionDraft,
    ResearchScenarioCoreDraft,
)
from tradingagents.research.synthesis.output_validation import OutputValidationError
from tradingagents.research.synthesis.references import REFERENCE_GUIDANCE, assemble_decision
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputResult,
    StructuredOutputRunner,
)


def _incremental_brief_fallback_title(language: ReportLanguage | str) -> str:
    """Return the deterministic heading used when a brief has no Markdown heading."""
    titles = {
        ReportLanguage.ENGLISH: "Incremental analysis",
        ReportLanguage.SIMPLIFIED_CHINESE: "增量分析",
        ReportLanguage.JAPANESE: "増分分析",
    }
    return titles[ReportLanguage(language)]


class _IncrementalAssessmentPayload(BaseModel):
    """Small Incremental assessment that decides whether a Decision is regenerated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reassessment: ResearchReassessment
    decision_outcome: IncrementalDecisionOutcome
    decision_outcome_reason: str = Field(min_length=1)
    full_research_required_reasons: tuple[FullResearchRequiredReason, ...] = ()


_FINAL_CONFIDENCE_PROSE_INSTRUCTION = (
    "Treat the structured Full Baseline Decision confidence level as authoritative. "
    "When discussing final Decision confidence, use only the localized equivalent of low, "
    "medium, or high. Never express final Decision confidence as a number, decimal, "
    "percentage, or probability, even when legacy baseline prose contains one."
)


class _IncrementalDecisionSection(BaseModel):
    """Bounded recovery section for the current qualitative Decision core."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ResearchDecisionCoreDraft


class _IncrementalDecisionPayload(BaseModel):
    """Complete Decision payload generated only for an updated outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: ResearchDecisionDraft


def _incremental_decision_core(decision: ResearchDecision) -> ResearchDecisionCoreDraft:
    return ResearchDecisionCoreDraft(
        rating=decision.rating,
        confidence=decision.confidence,
        executive_summary=decision.executive_summary,
        thesis=decision.thesis,
        evidence_refs=decision.evidence_refs,
        catalysts=decision.catalysts,
        risks=decision.risks,
        invalidation_conditions=decision.invalidation_conditions,
        unresolved_questions=decision.unresolved_questions,
        time_horizon=decision.time_horizon,
        scenarios=tuple(
            ResearchScenarioCoreDraft(
                kind=scenario.kind,
                core_assumptions=scenario.core_assumptions,
                outcome=scenario.outcome,
                evidence_refs=scenario.evidence_refs,
            )
            for scenario in decision.scenarios
        ),
        risk_review_adjustments=decision.risk_review_adjustments,
    )


def synthesize_incremental(
    synthesis_input: IncrementalSynthesisInput,
    *,
    run_settings: RunSettings,
    metrics: MetricsCallback,
    llm_factory: Callable[..., RunLLMs],
    event_writer: Callable[[dict[str, Any]], None],
) -> IncrementalSynthesis:
    """Use the run-scoped reasoning and serializer clients for required synthesis."""
    llms = llm_factory(
        run_settings,
        callbacks=[metrics],
        purpose="incremental",
    )
    semantic_llm = llms.deep
    serializer_llm = llms.deep_serializer

    output_language = report_language_prompt_label(run_settings.output_language)
    bounded_input = incremental_prompt_input(synthesis_input)
    semantic_prompt = (
        "Write a concise, user-facing Incremental Research analysis report. Assess every Full "
        "Baseline Decision Component using only the typed input. Do not use sibling "
        "Incremental Nodes or invent Evidence. Limited or missing optional Research "
        "Availability alone must not create a Full Research Required reason; do not "
        "reintroduce required-coverage certification. Cover the key new information, "
        "its effect on the current Decision, stock and benchmark Performance context, "
        "and unresolved questions. Keep audit metadata out of the narrative. "
        f"{_FINAL_CONFIDENCE_PROSE_INSTRUCTION} "
        f"Write all human-readable prose in {output_language}.\n\n" + bounded_input
    )
    with metrics.phase("incremental.synthesis.semantic", event_writer=event_writer):
        semantic_output = write_research_markdown(
            semantic_llm,
            prompt=semantic_prompt,
            node="incremental.synthesis.semantic",
            allowed_evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *synthesis_input.permitted_baseline_evidence_refs,
                        *(item.ref for item in synthesis_input.incremental_evidence.items),
                    )
                )
            ),
            output_language=output_language,
            allow_continuation=False,
            invoke_config={"metadata": {"research_node": "incremental.synthesis.semantic"}},
        )
    semantic_brief = semantic_output.markdown
    analysis_brief = IncrementalAnalysisBrief(
        markdown=semantic_brief,
        report_sections=parse_markdown_sections(
            semantic_brief,
            namespace="incremental",
            fallback_title=_incremental_brief_fallback_title(run_settings.output_language),
        ),
        evidence_refs=semantic_output.evidence_refs,
        warnings=semantic_output.warnings,
    )
    allowed_evidence_refs = tuple(
        dict.fromkeys(
            (
                *synthesis_input.permitted_baseline_evidence_refs,
                *(item.ref for item in synthesis_input.incremental_evidence.items),
            )
        )
    )
    expected_components = set(baseline_component_ids(synthesis_input.full_baseline_decision))

    def validate_assessment(
        value: _IncrementalAssessmentPayload,
    ) -> _IncrementalAssessmentPayload:
        if {entry.component_id for entry in value.reassessment.entries} != expected_components:
            raise OutputValidationError("incremental.reassessment.component_closure")
        if value.decision_outcome is IncrementalDecisionOutcome.UNCHANGED and any(
            entry.disposition is ReassessmentDisposition.OVERTURNED
            for entry in value.reassessment.entries
        ):
            raise OutputValidationError("incremental.outcome.overturned_unchanged")
        allowed = set(allowed_evidence_refs)
        if any(
            not set(entry.evidence_refs).issubset(allowed) for entry in value.reassessment.entries
        ) or any(
            not set(reason.evidence_refs).issubset(allowed)
            for reason in value.full_research_required_reasons
        ):
            raise OutputValidationError("incremental.assessment.refs_invalid")
        return value

    assessment_prompt = (
        "Serialize only the small Incremental assessment from the semantic brief and "
        "typed bounded input. Reassess every Full Baseline Decision Component. Set "
        "decision_outcome to unchanged when the complete baseline Decision remains valid "
        "without changing any field, even if evidence strengthened or weakened a component. "
        "Set it to updated only when at least one complete Decision field must actually be "
        "rewritten. An overturned component always requires updated. Do not serialize a "
        "Research Decision. Full Research Required is independent of decision_outcome, so "
        "either outcome may include a reason. Every reassessment entry and the outcome need "
        "a concise reason. Copy component_id exactly from baseline_component_ids; "
        "never construct IDs from section labels or prose. Include Evidence references "
        "only when the permitted bundles "
        "support them. Limited or missing optional Research Availability alone must not "
        "create a Full Research Required reason, and required_coverage codes are forbidden. "
        "Use only the typed reason codes for material thesis reversal, identity uncertainty, "
        "unreliable attribution, or material Evidence conflict. Write all human-readable "
        f"prose in {output_language}. {_FINAL_CONFIDENCE_PROSE_INSTRUCTION}\n\n"
        f"SEMANTIC BRIEF:\n{semantic_brief}\n\n"
        f"BOUNDED INPUT:\n{bounded_input}"
    )
    assessment_example = {
        "reassessment": {
            "entries": [
                {
                    "component_id": "thesis",
                    "disposition": "reaffirmed",
                    "reason": "Explain the bounded reassessment.",
                }
            ]
        },
        "decision_outcome": "unchanged",
        "decision_outcome_reason": "No complete Decision field needs to change.",
        "full_research_required_reasons": [],
    }
    with metrics.phase("incremental.synthesis.assessment", event_writer=event_writer):
        assessment_output = StructuredOutputRunner(
            llm=serializer_llm,
            schema=_IncrementalAssessmentPayload,
            validator=validate_assessment,
            node="incremental.synthesis.assessment",
            event_writer=event_writer,
            invoke_config={"metadata": {"research_node": "incremental.synthesis.assessment"}},
            repair_instructions=(
                "Write all human-readable prose in "
                f"{output_language}. Preserve every baseline component ID, enums, "
                "permitted Evidence refs, and typed Full Research Required codes exactly. "
                f"{_FINAL_CONFIDENCE_PROSE_INSTRUCTION}"
            ),
        ).invoke(
            assessment_prompt,
            example=assessment_example,
            allowed_evidence_refs=allowed_evidence_refs,
        )

    assessment = assessment_output.value
    baseline_decision = synthesis_input.full_baseline_decision
    if assessment.decision_outcome is IncrementalDecisionOutcome.UNCHANGED:
        decision = baseline_decision
    else:
        repair_available = not assessment_output.failed_attempts
        decision_prompt = (
            "Serialize one complete updated Research Decision from the semantic brief, "
            "small assessment, and typed bounded input. The Decision must differ from the "
            "Full Baseline in at least one real field. Do not emit a field patch or changed-"
            "fields list. Preserve supported current scenario ranges and market references. "
            f"{REFERENCE_GUIDANCE} Classify "
            "confidence by rubric, not as a probability: low means the core judgment remains "
            "tentative because important gaps, conflicts, or unresolved assumptions remain; "
            "medium means the main direction is supported but important uncertainty could "
            "materially change it; high means reliable evidence sufficiently supports the "
            "core judgment with no unresolved major conflict. Include exactly base, bull, "
            "and bear scenarios and use only permitted Evidence references. Write all human-"
            f"readable prose in {output_language}. {_FINAL_CONFIDENCE_PROSE_INSTRUCTION}\n\n"
            f"SEMANTIC BRIEF:\n{semantic_brief}\n\n"
            f"SMALL ASSESSMENT:\n{assessment.model_dump_json(indent=2)}\n\n"
            f"BOUNDED INPUT:\n{bounded_input}"
        )
        decision_example = {"decision": baseline_decision.model_dump(mode="json")}

        def validate_decision(
            value: _IncrementalDecisionPayload,
        ) -> _IncrementalDecisionPayload:
            candidate = assemble_decision(
                value.decision,
                bundle=synthesis_input.incremental_evidence,
                allowed_evidence_refs=set(allowed_evidence_refs),
                node="incremental.synthesis.decision",
            )
            if candidate == baseline_decision:
                raise OutputValidationError("incremental.decision.updated_identical")
            return value

        def decision_core_recovery() -> StructuredOutputResult[_IncrementalDecisionPayload]:
            decision_core = (
                StructuredOutputRunner(
                    llm=serializer_llm,
                    schema=_IncrementalDecisionSection,
                    validator=lambda value: value,
                    node="incremental.synthesis.decision",
                    event_writer=event_writer,
                    repair_enabled=False,
                    invoke_config={
                        "metadata": {
                            "research_node": "incremental.synthesis.decision",
                        }
                    },
                    repair_instructions=(
                        "Return one complete current qualitative Decision core using only "
                        "permitted Evidence references."
                    ),
                )
                .invoke(
                    (
                        "Recover only the complete updated qualitative Research Decision "
                        "core from the semantic brief and bounded input. Include exactly "
                        "base, bull, and bear scenarios. Do not serialize the Research "
                        "Reassessment, Full Research Required reasons, or any optional "
                        "reference fields. Unavailable optional references remain empty; never "
                        "copy baseline reference values into changed research automatically. "
                        f"{_FINAL_CONFIDENCE_PROSE_INSTRUCTION} "
                        f"Write all human-readable prose in {output_language}.\n\n"
                        f"SEMANTIC BRIEF:\n{semantic_brief}\n\n"
                        f"SMALL ASSESSMENT:\n{assessment.model_dump_json(indent=2)}\n\n"
                        f"BOUNDED INPUT:\n{bounded_input}"
                    ),
                    example={
                        "decision": _incremental_decision_core(baseline_decision).model_dump(
                            mode="json"
                        )
                    },
                    allowed_evidence_refs=allowed_evidence_refs,
                )
                .value
            )
            event_writer(
                {
                    "event_type": "decision.reference_omitted",
                    "node": "incremental.synthesis.decision",
                    "payload": {
                        "field_path": "optional_references",
                        "validation_issues": ["reference.core_only_recovery"],
                    },
                }
            )
            return StructuredOutputResult(
                value=_IncrementalDecisionPayload(
                    decision=ResearchDecisionDraft.model_validate(
                        decision_core.decision.model_dump()
                    )
                ),
                generation_method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
            )

        with metrics.phase("incremental.synthesis.decision", event_writer=event_writer):
            decision_output = StructuredOutputRunner(
                llm=serializer_llm,
                schema=_IncrementalDecisionPayload,
                validator=validate_decision,
                node="incremental.synthesis.decision",
                event_writer=event_writer,
                repair_enabled=repair_available,
                invoke_config={"metadata": {"research_node": "incremental.synthesis.decision"}},
                repair_instructions=(
                    "Return a complete Decision that really differs from the baseline, "
                    "uses only permitted Evidence refs, and follows the confidence rubric."
                    f" {_FINAL_CONFIDENCE_PROSE_INSTRUCTION}"
                ),
                truncation_recovery=(decision_core_recovery if repair_available else None),
                sectioned_recovery_reasons=("output_truncated", "schema_validation"),
                sectioned_recovery_after_repair=False,
            ).invoke(
                decision_prompt,
                example=decision_example,
                allowed_evidence_refs=allowed_evidence_refs,
            )
        decision = assemble_decision(
            decision_output.value.decision,
            bundle=synthesis_input.incremental_evidence,
            allowed_evidence_refs=set(allowed_evidence_refs),
            node="incremental.synthesis.decision",
            event_writer=event_writer,
        )

    return IncrementalSynthesis(
        analysis_brief=analysis_brief,
        reassessment=assessment.reassessment,
        decision_outcome=assessment.decision_outcome,
        decision_outcome_reason=assessment.decision_outcome_reason,
        decision=decision,
        full_research_required_reasons=assessment.full_research_required_reasons,
    )
