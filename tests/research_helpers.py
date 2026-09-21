"""Deterministic research substitutes used only by offline tests."""
from tradingagents.application.contracts import (
    IncrementalAnalysisBrief,
    IncrementalDecisionOutcome,
    IncrementalSynthesis,
    IncrementalSynthesisInput,
    ReassessmentDisposition,
    ResearchReassessment,
    ResearchReassessmentEntry,
)
from tradingagents.application.decision_components import baseline_component_ids
from tradingagents.application.llms import RunLLMs
from tradingagents.application.markdown_evidence import parse_markdown_sections


def stub_run_llms(*args, **kwargs):
    quick, deep = object(), object()
    return RunLLMs(quick, deep, quick, deep)


def default_incremental_synthesizer(
    synthesis_input: IncrementalSynthesisInput,
) -> IncrementalSynthesis:
    """Test-only deterministic seam; production always supplies a model-backed synthesizer."""
    return IncrementalSynthesis(
        analysis_brief=IncrementalAnalysisBrief(
            markdown=(
                "# Incremental analysis\n\n"
                "No material change was identified by the deterministic test synthesizer."
            ),
            report_sections=parse_markdown_sections(
                "# Incremental analysis\n\n"
                "No material change was identified by the deterministic test synthesizer.",
                namespace="incremental",
                fallback_title="Incremental analysis",
            ),
        ),
        reassessment=ResearchReassessment(
            entries=tuple(
                ResearchReassessmentEntry(
                    component_id=component_id,
                    disposition=ReassessmentDisposition.REAFFIRMED,
                    reason="The bounded update does not change this baseline component.",
                )
                for component_id in baseline_component_ids(synthesis_input.full_baseline_decision)
            )
        ),
        decision_outcome=IncrementalDecisionOutcome.UNCHANGED,
        decision_outcome_reason=(
            "The bounded update does not require any Full Decision field to change."
        ),
        decision=synthesis_input.full_baseline_decision,
    )
