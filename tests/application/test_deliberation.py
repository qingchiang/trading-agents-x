from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    AuditedRangeEndpoint,
    CalculationRecord,
    DebateAgenda,
    DebateImportance,
    DebateIssue,
    EvidenceBundle,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceTemporalScope,
    EvidenceValueLocator,
    JudgeDraft,
    MarketReferenceBasis,
    MarketReferenceLevel,
    MeasurementKind,
    NumericAuditAppendixStatus,
    NumericAuditStatus,
    NumericCalculationStatus,
    NumericDisplayScale,
    NumericDisplayStatus,
    NumericTemporalBasis,
    RebuttalReview,
    ReportLanguage,
    ResearchQuestionSourceDependency,
    ResearchScenarioKind,
    RiskReview,
    RiskReviewAdjustment,
    ScenarioReferenceCategory,
    ScenarioReferenceRange,
    ValuationAssessment,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.graph.deliberation import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    DerivedRangeEndpointDraft,
    InterpretedRangeEndpointDraft,
    ObservedMarketReferenceLevelDraft,
    ObservedRangeEndpointDraft,
    ResearchDecisionCoreDraft,
    ResearchDecisionCoreEnvelope,
    ScenarioReferenceRangeDraft,
    ScenarioReferenceRangesDraft,
    ValuationAssessmentDraft,
    _assemble_numeric_draft,
    _canonicalize_calculation_result,
    _emit_numeric_normalization_event,
    _evaluate_formula,
    _live_snapshot_date,
    _normalize_numeric_requirement_candidate,
    _numeric_audit_snapshot,
    _numeric_example_pair,
    _preflight_numeric_requirements,
    debate_round_has_material_progress,
    decision_reference_label_guidance,
    decision_scenario_assumption_guidance,
    invoke_debate_agenda,
    invoke_judge_draft,
    invoke_rebuttal,
    invoke_research_case,
    invoke_research_decision,
    invoke_risk_review,
    write_research_markdown,
)
from tradingagents.graph.numeric_evidence import build_numeric_value_catalog
from tradingagents.graph.output_validation import OutputValidationError
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputFailure,
)


class _StaticInvoker:
    def __init__(self, owner: _StaticLLM, schema: Any):
        self.owner = owner
        self.schema = schema

    def invoke(self, prompt: str, config: Any = None) -> dict[str, Any]:
        del config
        self.owner.prompts.append(prompt)
        parsed = self.owner.value
        if hasattr(parsed, "model_dump"):
            payload = parsed.model_dump(mode="json")
            if self.schema is ResearchDecisionCoreEnvelope:
                payload.pop("valuation_assessment", None)
                payload.pop("market_reference_levels", None)
                payload.pop("calculation_records", None)
                payload.pop("numeric_audit_status", None)
                for scenario in payload["scenarios"]:
                    scenario.pop("reference_ranges", None)
                parsed = ResearchDecisionCoreEnvelope.model_validate(
                    {
                        **payload,
                        "numeric_requirements_declared": False,
                        "numeric_requirement_candidates": [],
                    }
                )
            elif self.schema is DecisionNumericDraft:
                parsed = _numeric_draft_from_decision(payload)
        return {"raw": None, "parsed": parsed}


class _StaticLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, value: Any):
        self.value = value
        self.prompts: list[str] = []

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> _StaticInvoker:
        return _StaticInvoker(self, schema)


def _numeric_draft_from_decision(payload: dict[str, Any]) -> DecisionNumericDraft:
    scenario_reference_ranges: dict[str, list[dict[str, Any]]] = {
        "base": [],
        "bull": [],
        "bear": [],
    }
    for scenario in payload["scenarios"]:
        for reference_range in scenario.get("reference_ranges") or ():
            scenario_reference_ranges[scenario["kind"]].append(
                _range_draft(reference_range)
            )
    calculations = []
    for calculation in payload.get("calculation_records") or ():
        calculations.append(
            {
                key: value
                for key, value in calculation.items()
                if key
                not in {
                    "result",
                    "as_of_date",
                    "temporal_basis",
                    "decision_uses",
                    "date_evidence_refs",
                }
            }
            | {
                "inputs": [
                    {"name": name, "value": value} for name, value in calculation["inputs"].items()
                ],
            }
        )
    valuation = payload.get("valuation_assessment")
    if valuation is not None:
        valuation = {
            "method": valuation["method"],
            "low": _endpoint_draft(valuation["low"]),
            "high": _endpoint_draft(valuation["high"]),
            "limitations": valuation["limitations"],
        }
    references = [_reference_draft(item) for item in payload.get("market_reference_levels") or ()]
    has_content = bool(
        any(scenario_reference_ranges.values())
        or valuation
        or references
        or calculations
    )
    return DecisionNumericDraft.model_validate(
        {
            "requested": has_content,
            "scenario_reference_ranges": scenario_reference_ranges,
            "valuation_assessment": valuation,
            "market_reference_levels": references,
            "calculation_records": calculations,
        }
    )


def _endpoint_draft(endpoint: dict[str, Any]) -> dict[str, Any]:
    if endpoint["basis"] == "observed":
        return {
            "basis": "observed",
            "value_ref": _value_ref(endpoint),
        }
    if endpoint["basis"] == "interpreted":
        return {
            "basis": "interpreted",
            "value": endpoint["value"],
            "anchor_value_refs": (_interpreted_value_ref(endpoint),),
            "context_evidence_refs": (),
        }
    return {"basis": "derived", "calculation_id": endpoint["calculation_id"]}


def _range_draft(reference_range: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": reference_range["category"],
        "label": reference_range["label"],
        "low": _endpoint_draft(reference_range["low"]),
        "high": _endpoint_draft(reference_range["high"]),
        "interpretation": reference_range["interpretation"],
        "limitations": reference_range["limitations"],
    }


def _reference_draft(item: dict[str, Any]) -> dict[str, Any]:
    if item["basis"] == "observed":
        return {
            "label": item["label"],
            "value_ref": _value_ref(item),
            "interpretation": item["interpretation"],
            "basis": "observed",
        }
    if item["basis"] == "interpreted":
        return {
            "label": item["label"],
            "value": item["value"],
            "interpretation": item["interpretation"],
            "anchor_value_refs": (_interpreted_value_ref(item),),
            "context_evidence_refs": (),
            "basis": "interpreted",
        }
    return {
        "label": item["label"],
        "interpretation": item["interpretation"],
        "basis": "derived",
        "calculation_id": item["calculation_ids"][0],
    }


def _value_ref(item: dict[str, Any]) -> str:
    locator = EvidenceValueLocator.model_validate(item["source_locator"])
    evidence_item = EvidenceItem(
        ref=locator.evidence_ref,
        source="fixture",
        evidence_type="fixture scalar",
        requested_date=date(2026, 7, 24),
        effective_date=item.get("as_of_date"),
        value=item["value"],
        measurement_kind=item.get("measurement_kind", "unknown"),
        unit=item.get("unit"),
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(evidence_item,),
    )
    return build_numeric_value_catalog(bundle)[0].id


def _interpreted_value_ref(item: dict[str, Any]) -> str:
    evidence_ref = (item.get("date_evidence_refs") or item["evidence_refs"])[0]
    evidence_item = EvidenceItem(
        ref=evidence_ref,
        source="fixture",
        evidence_type="fixture interpreted anchor",
        requested_date=date(2026, 7, 24),
        effective_date=item.get("as_of_date"),
        value=item["value"],
        unit=item.get("unit"),
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(evidence_item,),
    )
    return build_numeric_value_catalog(bundle)[0].id


def _core_draft_from_decision(payload: dict[str, Any]) -> ResearchDecisionCoreDraft:
    payload = {**payload}
    payload.pop("valuation_assessment", None)
    payload.pop("market_reference_levels", None)
    payload.pop("calculation_records", None)
    payload.pop("numeric_audit_status", None)
    payload["scenarios"] = [
        {
            key: value
            for key, value in scenario.items()
            if key != "reference_ranges"
        }
        for scenario in payload["scenarios"]
    ]
    return ResearchDecisionCoreDraft.model_validate(payload)


def _core_envelope(
    core: ResearchDecisionCoreDraft,
    *,
    requirements: tuple[DecisionNumericRequirementDraft, ...] = (),
    declared: bool | None = None,
) -> ResearchDecisionCoreEnvelope:
    return ResearchDecisionCoreEnvelope.model_validate(
        {
            **core.model_dump(mode="json"),
            "numeric_requirements_declared": (
                bool(requirements) if declared is None else declared
            ),
            "numeric_requirement_candidates": [
                item.model_dump(mode="json") for item in requirements
            ],
        }
    )


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
    assert candidate["properties"]["inputs"]["items"] == {
        "$ref": "#/$defs/CalculationInputDraft"
    }
    component_pattern = candidate["properties"]["component_path"]["pattern"]
    assert "risks\\.\\d+" in component_pattern
    assert "catalysts\\.\\d+" in component_pattern
    assert "unresolved_questions" not in component_pattern
    assert "scenarios\\.(?:base|bull|bear)" in component_pattern
    display_scale_description = candidate["properties"]["display_scale"]["description"]
    assert "formula result only" in display_scale_description
    assert "Dimensionless" in display_scale_description


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


class _SequenceInvoker:
    def __init__(self, owner: _SequenceLLM, schema: Any):
        self.owner = owner
        self.schema = schema

    def invoke(self, prompt: str, config: Any = None) -> dict[str, Any]:
        del config
        self.owner.prompts.append((self.schema.__name__, prompt))
        response = self.owner.responses[self.schema.__name__].pop(0)
        if isinstance(response, dict) and (
            "raw" in response or "parsed" in response or "parsing_error" in response
        ):
            return response
        return {"raw": None, "parsed": response}


class _SequenceLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, responses: dict[str, list[Any]]):
        self.responses = responses
        self.prompts: list[tuple[str, str]] = []

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> _SequenceInvoker:
        return _SequenceInvoker(self, schema)


_NUMERIC_REGRESSION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "6501_numeric_audit.json"
)
_NUMERIC_3778_FIXTURE = (
    Path(__file__).parent / "fixtures" / "3778_numeric_normalization.json"
)
_NUMERIC_9984_FIXTURE = (
    Path(__file__).parent / "fixtures" / "9984_percentage_audit.json"
)


def _numeric_regression_payload() -> dict[str, Any]:
    return json.loads(_NUMERIC_REGRESSION_FIXTURE.read_text(encoding="utf-8"))


def _numeric_3778_payload() -> dict[str, Any]:
    return json.loads(_NUMERIC_3778_FIXTURE.read_text(encoding="utf-8"))


def _numeric_9984_payload() -> dict[str, Any]:
    return json.loads(_NUMERIC_9984_FIXTURE.read_text(encoding="utf-8"))


def _numeric_regression() -> tuple[EvidenceBundle, DecisionNumericDraft]:
    payload = _numeric_regression_payload()
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=payload["analysis_date"],
        items=tuple(EvidenceItem.model_validate(item) for item in payload["evidence"]),
        sealed_at=payload["sealed_at"],
    )
    return bundle, DecisionNumericDraft.model_validate(payload["numeric_candidate"])


def _numeric_noop_repair_candidate() -> DecisionNumericDraft:
    payload = _numeric_regression_payload()
    return DecisionNumericDraft.model_validate(payload["no_op_repair_candidate"])


def _value_catalog(bundle: EvidenceBundle) -> dict[str, Any]:
    return {item.id: item for item in build_numeric_value_catalog(bundle)}


def _state(*, content: str = "Fixture evidence.") -> dict[str, Any]:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market snapshot",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content=content,
        value=100,
        unit="USD",
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


def _live_numeric_fixture(
    *,
    ticker: str,
    analysis_date: date,
    retrieved_at: datetime,
    sealed_at: datetime | None = None,
) -> tuple[EvidenceBundle, DecisionNumericDraft | None]:
    origin = EvidenceOrigin(
        source="fixture.live",
        evidence_type="analyst consensus",
        requested=analysis_date.isoformat(),
        effective="live snapshot",
        timing="live-only retrieval",
        retrieved_at=retrieved_at.isoformat(),
        quality=EvidenceQuality.LOW,
        temporal_scope=EvidenceTemporalScope.LIVE_ONLY,
    )
    item = EvidenceItem.create(
        source="fixture.live",
        evidence_type="analyst consensus",
        requested_date=analysis_date,
        value=5500,
        unit="JPY",
        quality=EvidenceQuality.LOW,
        origins=(origin,),
    )
    bundle = EvidenceBundle(
        instrument=ticker,
        analysis_date=analysis_date,
        items=(item,),
        sealed_at=sealed_at or retrieved_at + timedelta(minutes=1),
    )
    catalog = build_numeric_value_catalog(bundle)
    draft = DecisionNumericDraft(
        requested=True,
        market_reference_levels=(
            ObservedMarketReferenceLevelDraft(
                label="Analyst target",
                value_ref=catalog[0].id,
                interpretation="Retrieval-time analyst consensus.",
            ),
        ),
    ) if catalog else None
    return bundle, draft


def test_research_markdown_uses_inline_ledger_refs_without_definitions() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    result = write_research_markdown(
        _MarkdownLLM(f"# Case\n\nSupported.[^{ref}]\n\n[^{ref}]: Model source text."),
        prompt="Write the case.",
        node="case.bull.write",
        allowed_evidence_refs=(ref,),
        output_language="English (en)",
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
    markdown = "## Constructive case\n\n| Measure | Reading |\n|---|---:|\n| Growth | 12.3% |\n"
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


def test_calculation_date_refs_ignore_undated_background_evidence() -> None:
    dated = EvidenceItem.create(
        source="statement",
        evidence_type="dated scalar",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=10,
    )
    background = EvidenceItem.create(
        source="news",
        evidence_type="undated background",
        requested_date=date(2026, 8, 1),
        content="Context only.",
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(dated, background),
    )
    requirement = DecisionNumericRequirementDraft(
        id="req_dated",
        component_path="thesis",
        label="Dated calculation",
        stated_value=20,
        fraction_digits=0,
        formula="value * multiplier",
        inputs=(
            CalculationInputDraft(
                name="value",
                value=10,
                date_evidence_refs=(dated.ref,),
            ),
            CalculationInputDraft(name="multiplier", value=2),
        ),
        input_evidence_refs=(dated.ref, background.ref),
        unit="JPY",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Background does not establish the input date.",),
    )
    draft = DecisionNumericDraft(
        requested=True,
        calculation_records=(
            CalculationRecordDraft(
                id="calc_dated",
                formula="value * multiplier",
                inputs=(
                    CalculationInputDraft(
                        name="value",
                        value=10,
                        date_evidence_refs=(dated.ref,),
                    ),
                    CalculationInputDraft(name="multiplier", value=2),
                ),
                input_evidence_refs=(dated.ref, background.ref),
                unit="JPY",
                limitations=("Background does not establish the input date.",),
                requirement_ids=(requirement.id,),
            ),
        ),
    )

    assembly = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={dated.ref, background.ref},
        value_catalog={},
        salvage=False,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    assert assembly.calculation_records[0].as_of_date == date(2026, 7, 31)
    assert assembly.calculation_records[0].date_evidence_refs == (dated.ref,)


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
    assert (
        result.numeric_generation_method
        is ArtifactGenerationMethod.TOOL_CALL_RECOVERED
    )
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


def test_core_declares_decision_critical_numeric_requirement() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core_draft = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    ).model_copy(
        update={"thesis": "The decision-critical earnings multiple is 82.1x."}
    )
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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 1
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1


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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 1


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
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1
    assert all(
        warning.code != "decision.numeric_repair_noop" for warning in result.warnings
    )
    core_prompt = next(
        prompt
        for schema, prompt in llm.prompts
        if schema == "ResearchDecisionCoreEnvelope"
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


def test_numeric_requirement_preflight_distinguishes_invalid_and_unbound_date_refs(
) -> None:
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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 1
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1


def test_4483_salvage_contains_invalid_date_ref_relationship() -> None:
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
    requirement = DecisionNumericRequirementDraft(
        id="req_invalid_date_ref",
        component_path="thesis",
        label="Invalid date ref",
        stated_value=50,
        fraction_digits=1,
        formula="value / divisor",
        inputs=(
            CalculationInputDraft(
                name="value",
                value=100,
                date_evidence_refs=(other.ref,),
            ),
            CalculationInputDraft(name="divisor", value=2),
        ),
        input_evidence_refs=(first_ref,),
        unit="x",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture limitation.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_invalid_date_ref",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    assembly = _assemble_numeric_draft(
        DecisionNumericDraft(
            requested=True,
            calculation_records=(calculation,),
        ),
        bundle=bundle,
        allowed_evidence_refs={first_ref, other.ref},
        value_catalog=build_numeric_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
        requirements=(requirement,),
    )

    check = assembly.requirement_checks[0]
    assert assembly.status is NumericAuditStatus.PARTIAL
    assert check.calculation_status is NumericCalculationStatus.INVALID
    assert check.display_status is NumericDisplayStatus.NOT_CHECKED
    assert check.date_evidence_refs == ()
    assert "numeric.requirement.req_invalid_date_ref.date_refs.not_input_refs" in (
        check.issue_codes
    )


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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 1
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1
    numeric_prompt = next(
        prompt for schema, prompt in llm.prompts if schema == "DecisionNumericDraft"
    )
    assert all(requirement.id in numeric_prompt for requirement in requirements)
    assert "req_invalid_sibling" not in numeric_prompt


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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 2


def test_missing_decision_calculation_degrades_numeric_audit_only_once() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    core_draft = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_dump(mode="json")
    ).model_copy(
        update={"thesis": "The remaining quarterly EPS is 16.08."}
    )
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
                CalculationInputDraft(
                    name="guidance_eps", value=values["company_guidance_eps"]
                ),
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
                CalculationInputDraft(
                    name="guidance_eps", value=values["company_guidance_eps"]
                ),
                CalculationInputDraft(
                    name="first_quarter_eps", value=values["first_quarter_eps"]
                ),
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
                CalculationInputDraft(
                    name="remaining_eps", value=values["stated_remaining_eps"]
                ),
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
    assert preflight.omissions[0].issue_codes == (
        "numeric.requirement_candidate.1.duplicate_id",
    )


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
            formula=(
                "(fy2026_net_income - fy2025_net_income) / fy2025_net_income"
            ),
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
            "ResearchDecisionCoreEnvelope": [
                _core_envelope(core, requirements=requirements)
            ],
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
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 1
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1


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
            "ResearchDecisionCoreEnvelope": [
                _core_envelope(core, requirements=requirements)
            ],
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
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1
    assert len(result.value.calculation_records) == 5
    expected = [item["expected_result"] for item in payload["requirements"]]
    assert [item.result for item in result.value.calculation_records] == pytest.approx(
        expected
    )
    assert all(item.decision_uses for item in result.value.calculation_records)
    serialized = json.loads(result.value.model_dump_json())
    assert [item["result"] for item in serialized["calculation_records"]] == pytest.approx(
        expected
    )


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


def test_display_mismatch_keeps_verified_calculation_and_comparison() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
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
    calculation = CalculationRecordDraft(
        id="calc_guidance_pe",
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

    assert result.status is NumericAuditStatus.PARTIAL
    assert len(result.calculation_records) == 1
    assert result.calculation_records[0].decision_uses[0].component_path == "thesis"
    assert result.omissions == ()
    assert result.audit is not None
    check = result.audit.requirement_checks[0]
    assert check.calculation_status is NumericCalculationStatus.VERIFIED
    assert check.display_status is NumericDisplayStatus.MISMATCHED
    assert check.stated_value == 45.8
    assert check.canonical_result == pytest.approx(3834.343755)
    assert check.rounded_stated_value == 45.8
    assert check.rounded_canonical_result == 3834.3
    assert check.issue_codes == (
        "numeric.requirement.req_guidance_pe.result_mismatch",
    )


def test_one_display_unit_difference_is_approximately_matched() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_growth",
        component_path="thesis",
        label="Revenue growth",
        stated_value=85.24,
        fraction_digits=2,
        formula="(current_revenue - prior_revenue) / prior_revenue",
        inputs=(
            CalculationInputDraft(name="current_revenue", value=185.22763378875221),
            CalculationInputDraft(name="prior_revenue", value=100),
        ),
        input_evidence_refs=(ref,),
        unit="%",
        display_scale=NumericDisplayScale.BASE,
        limitations=("Fixture calculation.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_growth",
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

    assert result.status is NumericAuditStatus.COMPLETE
    assert result.warnings == ()
    check = result.requirement_checks[0]
    assert check.display_status is NumericDisplayStatus.APPROXIMATELY_MATCHED
    assert check.rounded_stated_value == 85.24
    assert check.rounded_canonical_result == 85.23
    assert check.issue_codes == (
        "numeric.requirement.req_growth.display_approximate",
    )


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
            "ResearchDecisionCoreEnvelope": [
                _core_envelope(core, requirements=(requirement,))
            ],
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
    assert [schema for schema, _prompt in llm.prompts].count(
        "DecisionNumericDraft"
    ) == 1
    numeric_events = [
        event["event_type"]
        for event in events
        if event["event_type"].startswith("node.numeric_audit")
    ]
    assert numeric_events == ["node.numeric_audit_degraded"]


def test_percent_formula_using_display_scale_reports_specific_mismatch() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    requirement = DecisionNumericRequirementDraft(
        id="req_target_upside",
        component_path="thesis",
        label="Target upside",
        stated_value=45.46,
        fraction_digits=2,
        formula="((target_price - close_price) / close_price) * 100",
        inputs=(
            CalculationInputDraft(name="target_price", value=145.46),
            CalculationInputDraft(name="close_price", value=100),
        ),
        input_evidence_refs=(ref,),
        unit="%",
        display_scale=NumericDisplayScale.BASE,
        limitations=("The target may change.",),
    )
    calculation = CalculationRecordDraft(
        id="calc_target_upside",
        formula=requirement.formula,
        inputs=requirement.inputs,
        input_evidence_refs=requirement.input_evidence_refs,
        unit=requirement.unit,
        limitations=requirement.limitations,
        requirement_ids=(requirement.id,),
    )

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            DecisionNumericDraft(
                requested=True,
                calculation_records=(calculation,),
            ),
            bundle=bundle,
            allowed_evidence_refs={ref},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
            requirements=(requirement,),
        )

    assert (
        "numeric.requirement.req_target_upside.percent_scale_mismatch"
        in error.value.issue_codes
    )


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
        "formulas must return a fractional ratio" in prompt
        for _schema, prompt in llm.prompts
    )
    assert all(
        "union of inputs[*].date_evidence_refs must be a subset" in prompt
        for _schema, prompt in llm.prompts[:2]
    )
    assert all(
        "must never be inherited from an input's measurement scale" in prompt
        for _schema, prompt in llm.prompts
    )
    assert all(
        "display_scale=base, not million" in prompt
        for _schema, prompt in llm.prompts
    )


def test_internal_evidence_ref_as_question_source_triggers_core_repair() -> None:
    state = _state()
    ref = state["evidence_bundle"]["items"][0]["ref"]
    question = "Which filing will resolve the uncertainty?"
    core = _core_draft_from_decision(
        research_decision(evidence_refs=(ref,)).model_copy(
            update={"unresolved_questions": (question,)}
        ).model_dump(mode="json")
    )
    invalid = _core_envelope(core).model_copy(
        update={
            "question_source_dependencies": (
                ResearchQuestionSourceDependency(
                    question=question,
                    required_sources=(ref,),
                ),
            )
        }
    )
    repaired = _core_envelope(core).model_copy(
        update={
            "question_source_dependencies": (
                ResearchQuestionSourceDependency(
                    question=question,
                    required_sources=("EDINET",),
                ),
            )
        }
    )
    llm = _SequenceLLM(
        {
            "ResearchDecisionCoreEnvelope": [invalid, repaired],
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

    assert result.value.question_source_dependencies[0].required_sources == (
        "EDINET",
    )
    assert [schema for schema, _prompt in llm.prompts].count(
        "ResearchDecisionCoreEnvelope"
    ) == 2


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


@pytest.mark.parametrize(
    "case",
    _numeric_regression_payload()["scenario_alignment_cases"],
    ids=lambda case: f"{case['ticker']}-{case['label']}",
)
def test_explicit_cross_scenario_label_only_omits_that_range(
    case: dict[str, str],
) -> None:
    bundle, draft = _numeric_regression()
    valid_range = draft.scenario_reference_ranges.base[0]
    mismatched_range = valid_range.model_copy(update={"label": case["label"]})
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (valid_range, mismatched_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
        output_language=case["output_language"],
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        valid_range.label
    ]
    assert result.status is NumericAuditStatus.PARTIAL
    assert result.issues == (case["expected_issue"],)
    assert {item.component_path for item in result.omissions} == {
        "numeric.scenario.base.ranges.1"
    }


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


def test_non_scenario_purpose_label_is_not_treated_as_misaligned() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0].model_copy(
        update={"label": "下行风险参考区间"}
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range,)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
        output_language="Simplified Chinese (简体中文, zh-CN)",
    )

    assert result.scenario_reference_ranges[ResearchScenarioKind.BASE][0].label == (
        "下行风险参考区间"
    )
    assert result.status is NumericAuditStatus.COMPLETE


def test_6501_numeric_regression_canonicalizes_results_dates_and_shared_usage() -> None:
    bundle, draft = _numeric_regression()

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    calculations = {item.id: item for item in result.calculation_records}
    assert calculations["calc_current_pe"].result == pytest.approx(5267 / 201.14)
    assert calculations["calc_forward_pe"].result == pytest.approx(5267 / 178.13)
    assert calculations["calc_bull_price"].result == pytest.approx(29.76 * 201.14)
    assert calculations["calc_bear_price"].result == pytest.approx(25 * 178.13)
    assert {item.as_of_date for item in calculations.values()} == {date(2026, 7, 31)}
    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }
    assert sum(len(items) for items in result.scenario_reference_ranges.values()) == 4
    assert [
        item.label
        for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]
    ] == ["Base technical range", "Analyst target range"]
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.as_of_date == date(2026, 7, 31)
    assert result.valuation_assessment.measurement_kind is MeasurementKind.CURRENCY
    assert result.valuation_assessment.unit == "JPY"
    assert len(result.market_reference_levels) == 4
    assert {item.as_of_date for item in result.market_reference_levels} == {
        date(2026, 7, 31),
        date(2026, 8, 1),
    }
    assert result.market_reference_levels[-1].temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT
    assert set(result.valuation_assessment.calculation_ids) == {
        "calc_bear_price",
        "calc_bull_price",
    }
    assert "calc_current_pe" in result.market_reference_levels[1].calculation_ids
    assert result.status is NumericAuditStatus.COMPLETE
    base_ranges = result.scenario_reference_ranges[ResearchScenarioKind.BASE]
    assert base_ranges[0].low.as_of_date == date(2026, 7, 31)
    assert base_ranges[0].low.date_evidence_refs == ("ev_6501a0000001",)
    assert base_ranges[1].low.as_of_date == date(2026, 8, 1)
    assert base_ranges[1].low.temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT


def test_valuation_assessment_inherits_ratio_measurement_from_calculations() -> None:
    bundle, source = _numeric_regression()
    calculations = tuple(
        item
        for item in source.calculation_records
        if item.id in {"calc_current_pe", "calc_forward_pe"}
    )
    draft = DecisionNumericDraft(
        requested=True,
        valuation_assessment=ValuationAssessmentDraft(
            method="Forward earnings multiple range",
            low=DerivedRangeEndpointDraft(calculation_id="calc_forward_pe"),
            high=DerivedRangeEndpointDraft(calculation_id="calc_current_pe"),
            limitations=("The forward EPS remains an estimate.",),
        ),
        calculation_records=calculations,
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    valuation = result.valuation_assessment
    assert valuation is not None
    assert valuation.low.value < valuation.high.value
    assert valuation.measurement_kind is MeasurementKind.RATIO
    assert valuation.unit == "x"
    assert result.reordered_ranges == 1
    assert result.status is NumericAuditStatus.COMPLETE


def test_interpreted_range_date_uses_anchor_not_context_evidence() -> None:
    date_case = _numeric_regression_payload()["date_anchor_case"]
    bundle, _draft = _numeric_regression()
    market_item = bundle.items[0]
    context_item = EvidenceItem(
        ref="ev_6501a0000004",
        source="fixture.fundamentals",
        evidence_type="context only",
        requested_date=bundle.analysis_date,
        effective_date=date.fromisoformat(date_case["background_date"]),
        content="Context that explains the scenario but does not set its price date.",
    )
    bundle = bundle.model_copy(update={"items": (*bundle.items, context_item)})
    anchor_ref = build_numeric_value_catalog(bundle)[0].id
    endpoint = InterpretedRangeEndpointDraft(
        value=5000,
        anchor_value_refs=(anchor_ref,),
        context_evidence_refs=(context_item.ref,),
    )
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label="Technical support band",
                    low=endpoint,
                    high=endpoint.model_copy(update={"value": 5300}),
                    interpretation="A rounded technical reference range.",
                    limitations=("The range is not a forecast.",),
                ),
            )
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    reference_range = result.scenario_reference_ranges[ResearchScenarioKind.BASE][0]
    low = reference_range.low
    assert low.as_of_date == date.fromisoformat(date_case["expected_interpreted_date"])
    assert low.date_evidence_refs == (market_item.ref,)
    assert low.evidence_refs == (market_item.ref, context_item.ref)
    assert reference_range.measurement_kind is MeasurementKind.CURRENCY
    assert reference_range.unit == "JPY"


def test_valuation_label_requires_derived_calculation() -> None:
    bundle, draft = _numeric_regression()
    interpreted = draft.scenario_reference_ranges.base[0].model_copy(
        update={"label": "估值回归价格区间"}
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": ScenarioReferenceRangesDraft(
                base=(interpreted,)
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert result.issues == (
        "numeric.scenario.base.ranges.0.derived_calculation_required",
    )
    assert result.status is NumericAuditStatus.PARTIAL


def test_scenario_ranges_preserve_distinct_ranges_in_the_same_category() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0]
    second_range = base_range.model_copy(
        update={
            "label": "Secondary technical range",
            "low": base_range.low.model_copy(update={"value": 4600}),
            "high": base_range.high.model_copy(update={"value": 7100}),
        }
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range, second_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        "Base technical range",
        "Secondary technical range",
    ]
    assert result.status is NumericAuditStatus.COMPLETE


def test_exact_duplicate_scenario_range_is_removed_without_degrading_audit() -> None:
    bundle, draft = _numeric_regression()
    base_range = draft.scenario_reference_ranges.base[0]
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (base_range, base_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert len(result.scenario_reference_ranges[ResearchScenarioKind.BASE]) == 1
    assert result.status is NumericAuditStatus.COMPLETE
    assert result.issues == ()
    assert [warning.code for warning in result.warnings] == [
        "decision.numeric_duplicate_removed"
    ]


def test_observed_singleton_range_is_promoted_and_deduplicated_by_locator() -> None:
    regression = _numeric_regression_payload()["presentation_regressions"]
    rsi_case = regression["rsi"]
    singleton_case = regression["singleton_reference"]
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture.market",
        evidence_type="verified RSI",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        value=rsi_case["value"],
        measurement_kind=MeasurementKind(rsi_case["expected_measurement_kind"]),
        unit=rsi_case["expected_unit"],
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=(item,),
    )
    value_ref = build_numeric_value_catalog(bundle)[0].id
    endpoint = ObservedRangeEndpointDraft(value_ref=value_ref)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label=singleton_case["label"],
                    low=endpoint,
                    high=endpoint,
                    interpretation="Observed momentum reference.",
                    limitations=("A point is not a scenario range.",),
                ),
            )
        ),
        market_reference_levels=(
            ObservedMarketReferenceLevelDraft(
                label="Explicit RSI reference",
                value_ref=value_ref,
                interpretation="Explicit market reference wins deduplication.",
            ),
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert [level.label for level in result.market_reference_levels] == [
        "Explicit RSI reference"
    ]
    assert result.market_reference_levels[0].measurement_kind is MeasurementKind.INDEX
    assert result.promoted_singletons == 1
    assert singleton_case["same_locator"] is True
    assert singleton_case["expected_destination"] == "market_reference_levels"
    assert result.status is NumericAuditStatus.COMPLETE
    events: list[dict[str, Any]] = []
    _emit_numeric_normalization_event(
        result,
        event_writer=events.append,
        node="committee.final.serialize.numeric",
    )
    assert events == [
        {
            "event_type": "decision.numeric_singleton_promoted",
            "node": "committee.final.serialize.numeric",
            "payload": {"count": 1},
        }
    ]


def test_6501_scenario_assumption_regression_is_covered_by_prompt_guidance() -> None:
    regression = _numeric_regression_payload()["presentation_regressions"][
        "scenario_assumption"
    ]
    guidance = decision_scenario_assumption_guidance(
        ReportLanguage.SIMPLIFIED_CHINESE.prompt_label
    )

    assert regression["expected"] in guidance
    assert regression["metric_subject"] in guidance
    assert f"不要只写‘{regression['ambiguous']}’" in guidance


def test_equal_observed_values_with_different_locators_are_not_promoted() -> None:
    items = tuple(
        EvidenceItem(
            ref=ref,
            source="fixture.market",
            evidence_type=label,
            requested_date=date(2026, 8, 1),
            effective_date=date(2026, 7, 31),
            value=100,
            measurement_kind=MeasurementKind.CURRENCY,
            unit="JPY",
        )
        for ref, label in (
            ("ev_0123456789ab", "first source"),
            ("ev_abcdef012345", "second source"),
        )
    )
    bundle = EvidenceBundle(
        instrument="6501.T",
        analysis_date=date(2026, 8, 1),
        items=items,
    )
    catalog = build_numeric_value_catalog(bundle)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.TECHNICAL,
                    label="Conflicting singleton",
                    low=ObservedRangeEndpointDraft(value_ref=catalog[0].id),
                    high=ObservedRangeEndpointDraft(value_ref=catalog[1].id),
                    interpretation="Equal values from different locators.",
                    limitations=("The locators differ.",),
                ),
            )
        ),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in items},
        value_catalog={entry.id: entry for entry in catalog},
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.market_reference_levels == ()
    assert result.scenario_reference_ranges == {}
    assert result.issues == (
        "numeric.scenario.base.ranges.0.invalid_range",
        "numeric.requested.empty",
    )


def test_interpreted_singleton_is_promoted_to_interpreted_reference() -> None:
    bundle, draft = _numeric_regression()
    source_range = draft.scenario_reference_ranges.base[0]
    singleton = source_range.model_copy(
        update={"high": source_range.low.model_copy()}
    )
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(base=(singleton,)),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.scenario_reference_ranges == {}
    assert len(result.market_reference_levels) == 1
    reference = result.market_reference_levels[0]
    assert reference.basis is MarketReferenceBasis.INTERPRETED
    assert reference.value == singleton.low.value
    assert reference.source_locator is None
    assert result.status is NumericAuditStatus.COMPLETE


def test_derived_singleton_is_promoted_and_keeps_calculation() -> None:
    bundle, draft = _numeric_regression()
    calculation = draft.calculation_records[0]
    endpoint = DerivedRangeEndpointDraft(calculation_id=calculation.id)
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            base=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.FUNDAMENTAL,
                    label="Derived earnings reference",
                    low=endpoint,
                    high=endpoint,
                    interpretation="One derived valuation reference.",
                    limitations=("The input assumptions may change.",),
                ),
            )
        ),
        calculation_records=(calculation,),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert len(result.market_reference_levels) == 1
    reference = result.market_reference_levels[0]
    assert reference.basis is MarketReferenceBasis.DERIVED
    assert reference.calculation_ids == (calculation.id,)
    assert [item.id for item in result.calculation_records] == [calculation.id]
    assert result.status is NumericAuditStatus.COMPLETE


def test_reversed_range_and_valuation_are_canonically_ordered() -> None:
    bundle, draft = _numeric_regression()
    source_range = draft.scenario_reference_ranges.base[0]
    reversed_range = source_range.model_copy(
        update={"low": source_range.high, "high": source_range.low}
    )
    valuation = draft.valuation_assessment
    assert valuation is not None
    reversed_valuation = valuation.model_copy(
        update={"low": valuation.high, "high": valuation.low}
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": ScenarioReferenceRangesDraft(
                base=(reversed_range,)
            ),
            "valuation_assessment": reversed_valuation,
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assembled_range = result.scenario_reference_ranges[ResearchScenarioKind.BASE][0]
    assert assembled_range.low.value < assembled_range.high.value
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.low.value < result.valuation_assessment.high.value
    assert result.reordered_ranges == 2
    assert result.status is NumericAuditStatus.COMPLETE
    events: list[dict[str, Any]] = []
    _emit_numeric_normalization_event(
        result,
        event_writer=events.append,
        node="committee.final.serialize.numeric",
    )
    assert events == [
        {
            "event_type": "decision.numeric_range_reordered",
            "node": "committee.final.serialize.numeric",
            "payload": {"count": 2},
        }
    ]


def test_3778_singleton_and_reversed_pe_valuation_normalize_without_repair() -> None:
    payload = _numeric_3778_payload()
    evidence = payload["evidence"]
    items = tuple(
        EvidenceItem(
            ref=item["ref"],
            source="fixture.3778",
            evidence_type=name,
            requested_date=payload["analysis_date"],
            effective_date=item["effective_date"],
            value=item["value"],
            measurement_kind=item["measurement_kind"],
            unit=item["unit"],
        )
        for name, item in evidence.items()
    )
    bundle = EvidenceBundle(
        instrument=payload["instrument"],
        analysis_date=payload["analysis_date"],
        items=items,
    )
    catalog = build_numeric_value_catalog(bundle)
    by_ref = {entry.locator.evidence_ref: entry.id for entry in catalog}
    price = evidence["price"]
    company_eps = evidence["company_eps"]
    consensus_eps = evidence["consensus_eps"]
    singleton = payload["interpreted_singleton"]
    valuation = payload["reversed_valuation"]
    calculations = (
        CalculationRecordDraft(
            id="calc_company_pe",
            formula=valuation["low_calculation"],
            inputs=(
                CalculationInputDraft(name="price", value=price["value"]),
                CalculationInputDraft(name="company_eps", value=company_eps["value"]),
            ),
            input_evidence_refs=(price["ref"], company_eps["ref"]),
            unit=valuation["unit"],
            limitations=("Company guidance may change.",),
        ),
        CalculationRecordDraft(
            id="calc_consensus_pe",
            formula=valuation["high_calculation"],
            inputs=(
                CalculationInputDraft(name="price", value=price["value"]),
                CalculationInputDraft(name="consensus_eps", value=consensus_eps["value"]),
            ),
            input_evidence_refs=(price["ref"], consensus_eps["ref"]),
            unit=valuation["unit"],
            limitations=("Consensus coverage is limited.",),
        ),
    )
    endpoint = InterpretedRangeEndpointDraft(
        value=singleton["value"],
        anchor_value_refs=(by_ref[consensus_eps["ref"]],),
    )
    draft = DecisionNumericDraft(
        requested=True,
        scenario_reference_ranges=ScenarioReferenceRangesDraft(
            bull=(
                ScenarioReferenceRangeDraft(
                    category=ScenarioReferenceCategory.ANALYST_CONSENSUS,
                    label=singleton["label"],
                    low=endpoint,
                    high=endpoint,
                    interpretation="Consensus EPS is a point reference.",
                    limitations=("Coverage is limited.",),
                ),
            )
        ),
        valuation_assessment=ValuationAssessmentDraft(
            method=valuation["method"],
            low=DerivedRangeEndpointDraft(calculation_id="calc_company_pe"),
            high=DerivedRangeEndpointDraft(calculation_id="calc_consensus_pe"),
            limitations=("Both EPS inputs may change.",),
        ),
        calculation_records=calculations,
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in items},
        value_catalog={entry.id: entry for entry in catalog},
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    assert result.status is NumericAuditStatus.COMPLETE
    assert result.promoted_singletons == 1
    assert result.reordered_ranges == 1
    assert result.scenario_reference_ranges == {}
    assert result.market_reference_levels[0].value == singleton["value"]
    assert result.valuation_assessment is not None
    assert result.valuation_assessment.measurement_kind is MeasurementKind.RATIO
    assert result.valuation_assessment.unit == valuation["unit"]
    assert result.valuation_assessment.low.value == pytest.approx(valuation["expected_low"])
    assert result.valuation_assessment.high.value == pytest.approx(valuation["expected_high"])
    assert payload["no_op_repair"]["initial_digest"] == payload["no_op_repair"]["repair_digest"]


def test_invalid_scenario_range_only_omits_that_range() -> None:
    bundle, draft = _numeric_regression()
    valid_range = draft.scenario_reference_ranges.base[0]
    invalid_range = valid_range.model_copy(
        update={
            "label": "Invalid unsupported range",
            "low": valid_range.low.model_copy(
                update={"anchor_value_refs": ("nv_ffffffffffff",)}
            ),
        }
    )
    draft = draft.model_copy(
        update={
            "scenario_reference_ranges": draft.scenario_reference_ranges.model_copy(
                update={"base": (valid_range, invalid_range)}
            )
        }
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert [item.label for item in result.scenario_reference_ranges[ResearchScenarioKind.BASE]] == [
        "Base technical range"
    ]
    assert result.status is NumericAuditStatus.PARTIAL
    assert {item.component_path for item in result.omissions} == {
        "numeric.scenario.base.ranges.1"
    }
    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }


def test_descriptive_pseudo_formula_does_not_remove_observed_scenario_ranges() -> None:
    bundle, draft = _numeric_regression()
    invalid = CalculationRecordDraft(
        id="calc_descriptive_band",
        formula="ema_to_bollinger",
        inputs=(
            CalculationInputDraft(name="ema", value=5000),
            CalculationInputDraft(name="bollinger", value=5500),
        ),
        input_evidence_refs=(bundle.items[0].ref,),
        unit="JPY",
        limitations=("Descriptive fixture.",),
    )
    draft = draft.model_copy(
        update={"calculation_records": (*draft.calculation_records, invalid)}
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={item.ref for item in bundle.items},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert set(result.scenario_reference_ranges) == {
        ResearchScenarioKind.BASE,
        ResearchScenarioKind.BULL,
        ResearchScenarioKind.BEAR,
    }
    assert all(item.id != "calc_descriptive_band" for item in result.calculation_records)
    assert (
        "numeric.calculation.calc_descriptive_band.formula.missing_input"
        in result.issues
    )


@pytest.mark.parametrize(
    ("ticker", "analysis_date", "retrieved_at", "expected_date"),
    (
        ("6501.T", date(2026, 8, 1), "2026-08-01T01:00:00+00:00", date(2026, 8, 1)),
        ("600519.SS", date(2026, 8, 1), "2026-08-01T01:00:00+00:00", date(2026, 8, 1)),
        ("NVDA", date(2026, 7, 31), "2026-08-01T01:00:00+00:00", date(2026, 7, 31)),
    ),
)
def test_live_numeric_evidence_uses_market_local_snapshot_date(
    ticker: str,
    analysis_date: date,
    retrieved_at: str,
    expected_date: date,
) -> None:
    bundle, draft = _live_numeric_fixture(
        ticker=ticker,
        analysis_date=analysis_date,
        retrieved_at=datetime.fromisoformat(retrieved_at),
    )
    assert draft is not None

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=False,
        node="committee.final.serialize.numeric",
    )

    reference = result.market_reference_levels[0]
    assert reference.as_of_date == expected_date
    assert reference.temporal_basis is NumericTemporalBasis.LIVE_SNAPSHOT


@pytest.mark.parametrize(
    ("age_days", "accepted"),
    ((0, True), (5, True), (6, False), (-1, False)),
)
def test_live_numeric_evidence_enforces_near_live_window(
    age_days: int,
    accepted: bool,
) -> None:
    retrieved_at = datetime(2026, 8, 6, 12, tzinfo=UTC)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 6) - timedelta(days=age_days),
        retrieved_at=retrieved_at,
    )

    if not accepted:
        assert draft is None
        assert bundle.items[0].quality is EvidenceQuality.UNAVAILABLE
        assert _live_snapshot_date(bundle.items[0], bundle=bundle) is None
        return
    assert draft is not None

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert bool(result.market_reference_levels) is accepted
    if not accepted:
        assert "numeric.market_reference.0.date_unavailable" in result.issues


def test_live_numeric_evidence_rejects_retrieval_after_seal() -> None:
    retrieved_at = datetime(2026, 8, 1, 1, tzinfo=UTC)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 1),
        retrieved_at=retrieved_at,
        sealed_at=retrieved_at - timedelta(seconds=1),
    )
    assert draft is None
    assert bundle.items[0].quality is EvidenceQuality.UNAVAILABLE
    assert bundle.items[0].provenance["evidence_admission"]["reason"] == (
        "retrieved_after_seal"
    )
    assert _live_snapshot_date(bundle.items[0], bundle=bundle) is None


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
    assert (
        result.numeric_generation_method
        is ArtifactGenerationMethod.TOOL_CALL_RECOVERED
    )
    assert len(result.numeric_audit.snapshots) == 2
    assert (
        result.numeric_audit.snapshots[0].candidate_digest
        == result.numeric_audit.snapshots[1].candidate_digest
    )
    assert [event["event_type"] for event in events] == [
        "node.numeric_audit_retry",
        "node.numeric_audit_degraded",
    ]
    assert any(
        warning.code == "decision.numeric_repair_noop"
        for warning in result.warnings
    )


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    (
        ("missing_date", "numeric.calculation.calc_current_pe.date_unavailable"),
        ("invalid_ref", "refs.invalid"),
        ("invalid_formula", "numeric.calculation.calc_current_pe.formula.invalid_syntax"),
        ("division_by_zero", "numeric.calculation.calc_current_pe.formula.division_by_zero"),
    ),
)
def test_6501_numeric_regression_keeps_strict_failure_boundaries(
    mutation: str,
    expected_issue: str,
) -> None:
    bundle, draft = _numeric_regression()
    first = draft.calculation_records[0]
    if mutation == "missing_date":
        missing_ref = first.input_evidence_refs[-1]
        bundle = bundle.model_copy(
            update={
                "items": tuple(
                    item.model_copy(update={"effective_date": None})
                    if item.ref == missing_ref
                    else item
                    for item in bundle.items
                )
            }
        )
    elif mutation == "invalid_ref":
        first = first.model_copy(update={"input_evidence_refs": ("ev_ffffffffffff",)})
    elif mutation == "invalid_formula":
        first = first.model_copy(update={"formula": "price +"})
    else:
        first = first.model_copy(
            update={
                "formula": "price / divisor",
                "inputs": (
                    CalculationInputDraft(name="price", value=5267),
                    CalculationInputDraft(name="divisor", value=0),
                ),
            }
        )
    if mutation != "missing_date":
        draft = draft.model_copy(
            update={"calculation_records": (first, *draft.calculation_records[1:])}
        )

    with pytest.raises(OutputValidationError) as error:
        _assemble_numeric_draft(
            draft,
            bundle=bundle,
            allowed_evidence_refs={item.ref for item in bundle.items},
            value_catalog=_value_catalog(bundle),
            salvage=False,
            node="committee.final.serialize.numeric",
        )

    assert expected_issue in error.value.issue_codes


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


def test_numeric_audit_snapshot_redacts_secrets_and_omits_oversize_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tradingagents.graph.deliberation._NUMERIC_CANDIDATE_MAX_BYTES",
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
