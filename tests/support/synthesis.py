"""Deterministic model responses and source fixtures."""
from __future__ import annotations

from datetime import date
from typing import Any

from tests.support.factories import analyst_report
from tradingagents.domain.common import DebateImportance
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.domain.reports import DebateAgenda, DebateIssue
from tradingagents.research.synthesis.drafts import ResearchDecisionCoreDraft


class _StaticInvoker:
    def __init__(self, owner, schema):
        self.owner, self.schema = owner, schema

    def invoke(self, prompt, config=None):
        self.owner.prompts.append(prompt)
        value = self.owner.value
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return {"raw": None, "parsed": value.model_dump(mode="json") if hasattr(value, "model_dump") else value}


class _StaticLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, value: Any):
        self.value = value
        self.prompts: list[str] = []

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> _StaticInvoker:
        return _StaticInvoker(self, schema)

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
        return {"raw": None, "parsed": response.model_dump(mode="json") if hasattr(response, "model_dump") else response}

class _SequenceLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, responses: dict[str, list[Any]]):
        self.responses = responses
        self.prompts: list[tuple[str, str]] = []

    def with_structured_output(self, schema: Any, **_kwargs: Any) -> _SequenceInvoker:
        return _SequenceInvoker(self, schema)

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

def _core_draft_from_decision(payload: dict[str, Any]) -> ResearchDecisionCoreDraft:
    payload = {**payload}
    payload.pop("market_reference_levels", None)
    payload["scenarios"] = [
        {key: value for key, value in scenario.items() if key != "reference_ranges"}
        for scenario in payload["scenarios"]
    ]
    return ResearchDecisionCoreDraft.model_validate(payload)
