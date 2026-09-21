"""Deterministic model responses and numeric source fixtures."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.support.factories import analyst_report
from tradingagents.domain.common import (
    DebateImportance,
)
from tradingagents.domain.decision import (
    EvidenceValueLocator,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceQuality,
    EvidenceTemporalScope,
)
from tradingagents.domain.reports import (
    DebateAgenda,
    DebateIssue,
)
from tradingagents.research.synthesis.drafts import (
    DecisionNumericDraft,
    DecisionNumericRequirementDraft,
    ObservedMarketReferenceLevelDraft,
    ResearchDecisionCoreDraft,
    ResearchDecisionCoreEnvelope,
)
from tradingagents.research.synthesis.numeric_evidence import build_numeric_value_catalog


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
            scenario_reference_ranges[scenario["kind"]].append(_range_draft(reference_range))
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
        any(scenario_reference_ranges.values()) or valuation or references or calculations
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
        {key: value for key, value in scenario.items() if key != "reference_ranges"}
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
            "numeric_requirements_declared": (bool(requirements) if declared is None else declared),
            "numeric_requirement_candidates": [
                item.model_dump(mode="json") for item in requirements
            ],
        }
    )


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
    Path(__file__).parents[1] / "research" / "fixtures" / "6501_numeric_audit.json"
)


_NUMERIC_3778_FIXTURE = (
    Path(__file__).parents[1] / "research" / "fixtures" / "3778_numeric_normalization.json"
)


_NUMERIC_9984_FIXTURE = (
    Path(__file__).parents[1] / "research" / "fixtures" / "9984_percentage_audit.json"
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
