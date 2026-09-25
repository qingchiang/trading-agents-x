"""Deterministic research substitutes used only by offline tests."""
from tradingagents.domain.decision_components import baseline_component_ids
from tradingagents.domain.incremental import (
    IncrementalAnalysisBrief,
    IncrementalDecisionOutcome,
    IncrementalSynthesis,
    IncrementalSynthesisInput,
    ReassessmentDisposition,
    ResearchReassessment,
    ResearchReassessmentEntry,
)
from tradingagents.llm.runtime import RunLLMs
from tradingagents.research.presentation import parse_markdown_sections


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


def model_settings(provider='openai', *, quick='gpt-5.4-mini', deep='gpt-5.5', quick_effort=None, deep_effort=None, **options):
    """Build current bound settings for client-construction tests."""
    from tradingagents.configuration.defaults import build_default_config
    from tradingagents.configuration.settings import RunSettings
    from tradingagents.llm.models import ModelBinding, preset_connection

    connection = preset_connection(provider, identity='test')
    if connection.transport.kind != 'bedrock':
        fields = connection.transport.model_dump()
        fields['base_url'] = fields['base_url'] or 'https://test.invalid/v1'
        if fields['kind'] == 'azure':
            fields['api_version'] = '2024-10-21'
        connection = type(connection).model_validate({**connection.model_dump(), 'transport': fields})
    return RunSettings(
        quick_binding=ModelBinding(connection=connection, model=quick, reasoning_effort=quick_effort),
        deep_binding=ModelBinding(connection=connection, model=deep, reasoning_effort=deep_effort),
        data_config=options.pop('data_config', build_default_config()), **options,
    )
