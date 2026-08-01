from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
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
    NumericAuditAppendixStatus,
    NumericAuditStatus,
    NumericTemporalBasis,
    RebuttalReview,
    ResearchScenarioKind,
    RiskReview,
    ScenarioReferenceCategory,
    ScenarioReferenceRange,
    ValuationAssessment,
)
from tradingagents.application.metrics import MetricsCallback
from tradingagents.graph.deliberation import (
    CalculationInputDraft,
    CalculationRecordDraft,
    DecisionNumericDraft,
    InterpretedRangeEndpointDraft,
    ObservedMarketReferenceLevelDraft,
    ResearchDecisionCoreDraft,
    ScenarioReferenceRangeDraft,
    ScenarioReferenceRangesDraft,
    _assemble_numeric_draft,
    _evaluate_formula,
    _numeric_audit_snapshot,
    debate_round_has_material_progress,
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
            if self.schema is ResearchDecisionCoreDraft:
                payload.pop("valuation_assessment", None)
                payload.pop("market_reference_levels", None)
                payload.pop("calculation_records", None)
                payload.pop("numeric_audit_status", None)
                for scenario in payload["scenarios"]:
                    scenario.pop("reference_ranges", None)
                parsed = ResearchDecisionCoreDraft.model_validate(payload)
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
                if key not in {"result", "as_of_date", "temporal_basis"}
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
            "currency": valuation["currency"],
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
        "unit": reference_range["unit"],
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
            "unit": item["unit"],
            "interpretation": item["interpretation"],
            "anchor_value_refs": (_interpreted_value_ref(item),),
            "context_evidence_refs": (),
            "basis": "interpreted",
        }
    return {
        "label": item["label"],
        "unit": item["unit"],
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


def _numeric_regression_payload() -> dict[str, Any]:
    return json.loads(_NUMERIC_REGRESSION_FIXTURE.read_text(encoding="utf-8"))


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
) -> tuple[EvidenceBundle, DecisionNumericDraft]:
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
    draft = DecisionNumericDraft(
        requested=True,
        market_reference_levels=(
            ObservedMarketReferenceLevelDraft(
                label="Analyst target",
                value_ref=build_numeric_value_catalog(bundle)[0].id,
                interpretation="Retrieval-time analyst consensus.",
            ),
        ),
    )
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
                currency="USD",
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
            "ResearchDecisionCoreDraft": [core],
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


@pytest.mark.parametrize(
    ("output_language", "localized_example"),
    (
        ("Simplified Chinese (简体中文, zh-CN)", "现有证据支持一项平衡的研究结论"),
        ("使用正式、克制的繁体中文", "The evidence supports a balanced conclusion"),
    ),
)
def test_final_serializers_preserve_output_language_in_primary_and_repair(
    output_language: str,
    localized_example: str,
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
            "ResearchDecisionCoreDraft": [invalid_core, core],
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
            "ResearchDecisionCoreDraft": [core],
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
                    unit="JPY",
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

    low = result.scenario_reference_ranges[ResearchScenarioKind.BASE][0].low
    assert low.as_of_date == date.fromisoformat(date_case["expected_interpreted_date"])
    assert low.date_evidence_refs == (market_item.ref,)
    assert low.evidence_refs == (market_item.ref, context_item.ref)


def test_currency_valuation_label_requires_derived_calculation() -> None:
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
        ("BTC-USD", date(2026, 8, 1), "2026-08-01T01:00:00+00:00", date(2026, 8, 1)),
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
    retrieved_at = datetime(2026, 8, 6, 12, tzinfo=timezone.utc)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 6) - timedelta(days=age_days),
        retrieved_at=retrieved_at,
    )

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
    retrieved_at = datetime(2026, 8, 1, 1, tzinfo=timezone.utc)
    bundle, draft = _live_numeric_fixture(
        ticker="6501.T",
        analysis_date=date(2026, 8, 1),
        retrieved_at=retrieved_at,
        sealed_at=retrieved_at - timedelta(seconds=1),
    )

    result = _assemble_numeric_draft(
        draft,
        bundle=bundle,
        allowed_evidence_refs={bundle.items[0].ref},
        value_catalog=_value_catalog(bundle),
        salvage=True,
        node="committee.final.serialize.numeric",
    )

    assert result.market_reference_levels == ()
    assert "numeric.market_reference.0.date_unavailable" in result.issues


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
            "ResearchDecisionCoreDraft": [core],
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
            "ResearchDecisionCoreDraft": [core],
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
                                    value=999,
                                    basis=MarketReferenceBasis.DERIVED,
                                    evidence_refs=(ref,),
                                    date_evidence_refs=(ref,),
                                    calculation_id="calc_scenario",
                                    as_of_date=date(2026, 7, 24),
                                ),
                                high=AuditedRangeEndpoint(
                                    value=999,
                                    basis=MarketReferenceBasis.DERIVED,
                                    evidence_refs=(ref,),
                                    date_evidence_refs=(ref,),
                                    calculation_id="calc_scenario",
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
                    id="calc_scenario",
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
    assert result.value.calculation_records[0].result == pytest.approx(110.0)
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
                currency="USD",
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
