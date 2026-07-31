from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    MemoryContext,
)
from tradingagents.graph.deliberation import invoke_research_decision
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputRunner,
)

_REF = "ev_0123456789ab"


class _Review(BaseModel):
    role: str
    thesis: str
    evidence_refs: tuple[str, ...]
    risks: tuple[str, ...]


def _review(**updates: Any) -> _Review:
    values = {
        "role": "bear",
        "thesis": "The evidence supports a skeptical review.",
        "evidence_refs": (_REF,),
        "risks": ("The downside mechanism may not materialize.",),
    }
    values.update(updates)
    return _Review(**values)


class _Invoker:
    def __init__(self, owner: _FakeLLM, method: str, response: Any):
        self.owner = owner
        self.method = method
        self.response = response

    def invoke(self, prompt: str, config: Any = None) -> Any:
        del config
        self.owner.calls.append((self.method, prompt))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _FakeLLM:
    def __init__(
        self,
        *,
        primary: Any,
        recovery: Any,
        plain_recovery: Any | None = None,
        preferred_method: str = "function_calling",
        reject_json_binding: bool = False,
    ) -> None:
        self.primary = primary
        self.recovery = recovery
        self.plain_recovery = plain_recovery
        self.preferred_structured_output_method = preferred_method
        self.reject_json_binding = reject_json_binding
        self.calls: list[tuple[str, str]] = []

    def with_structured_output(
        self,
        _schema: Any,
        *,
        method: str | None = None,
        include_raw: bool = False,
        **_kwargs: Any,
    ) -> _Invoker:
        assert include_raw is True
        if method == "json_mode" and self.reject_json_binding:
            raise ValueError("json mode unsupported")
        resolved = method or (
            "json_mode"
            if self.preferred_structured_output_method == "json_mode"
            else "tool_call"
        )
        response = self.recovery if method == "json_mode" else self.primary
        return _Invoker(self, resolved, response)

    def invoke(self, prompt: str, **_kwargs: Any) -> Any:
        self.calls.append(("prompt_json", prompt))
        if isinstance(self.plain_recovery, BaseException):
            raise self.plain_recovery
        return self.plain_recovery


def _validate(value: _Review) -> _Review:
    if not value.risks:
        raise ValueError("missing risks")
    if set(value.evidence_refs) != {_REF}:
        raise ValueError("invalid evidence refs")
    return value


def _runner(llm: _FakeLLM, events: list[dict[str, Any]]) -> StructuredOutputRunner[_Review]:
    return StructuredOutputRunner(
        llm=llm,
        schema=_Review,
        validator=_validate,
        node="case.bear",
        event_writer=events.append,
    )


def _invoke(runner: StructuredOutputRunner[_Review]):
    return runner.invoke(
        "Produce a bearish review.",
        example=_review().model_dump(mode="json"),
        allowed_evidence_refs=(_REF,),
    )


def test_first_typed_output_succeeds_with_one_logical_call() -> None:
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
        recovery=AssertionError("recovery must not run"),
    )

    result = _invoke(_runner(llm, events))

    assert result.value == _review()
    assert result.generation_method is ArtifactGenerationMethod.TOOL_CALL
    assert [method for method, _prompt in llm.calls] == ["tool_call"]
    assert events == []


def test_raw_json_is_recovered_without_second_call() -> None:
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=json.dumps(_review().model_dump(mode="json"))),
            "parsed": None,
            "parsing_error": ValueError("parser failed"),
        },
        recovery=AssertionError("recovery must not run"),
    )

    result = _invoke(_runner(llm, []))

    assert result.value == _review()
    assert result.generation_method is ArtifactGenerationMethod.RAW_JSON_RECOVERED
    assert len(llm.calls) == 1


def test_json_mode_recovery_succeeds_with_two_calls() -> None:
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content="not json"),
            "parsed": None,
            "parsing_error": ValueError("invalid"),
        },
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is ArtifactGenerationMethod.JSON_MODE_RECOVERED
    assert [method for method, _prompt in llm.calls] == [
        "tool_call",
        "json_mode",
    ]
    assert [event["event_type"] for event in events] == [
        "node.output_retry",
        "node.output_recovered",
    ]


def test_candidate_only_repair_omits_the_original_task() -> None:
    original_task = "FULL RESEARCH CONTEXT MUST NOT BE REPEATED"
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=""),
            "parsed": _review(risks=()),
            "parsing_error": None,
        },
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )
    runner = StructuredOutputRunner(
        llm=llm,
        schema=_Review,
        validator=_validate,
        node="case.bear",
        include_candidate_in_repair=True,
        candidate_only_repair=True,
    )

    result = runner.invoke(
        original_task,
        example=_review().model_dump(mode="json"),
        allowed_evidence_refs=(_REF,),
    )

    assert result.value == _review()
    recovery_prompt = llm.calls[1][1]
    assert "INVALID CANDIDATE JSON" in recovery_prompt
    assert original_task not in recovery_prompt


def test_two_invalid_outputs_fail_without_leaking_provider_content() -> None:
    secret = "token=private-value"
    response = {
        "raw": AIMessage(content=secret),
        "parsed": _review(risks=()),
        "parsing_error": ValueError(secret),
    }
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(StructuredOutputError) as error:
        _invoke(_runner(llm, events))

    assert error.value.reason_code == "semantic_validation"
    assert secret not in str(error.value)
    assert events[-1]["event_type"] == "node.output_failed"
    assert secret not in json.dumps(events)


def test_truncated_primary_output_uses_specific_recovery_reason() -> None:
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(
                content="",
                response_metadata={"finish_reason": "length"},
            ),
            "parsed": None,
            "parsing_error": ValueError("truncated"),
        },
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )

    _invoke(_runner(llm, events))

    assert events[0]["payload"]["reason_code"] == "output_truncated"


def _decision_payload(evidence_ref: str) -> dict[str, Any]:
    return {
        "rating": "Hold",
        "confidence": 0.5,
        "executive_summary": "The evidence supports a balanced conclusion.",
        "thesis": "The current evidence supports a balanced conclusion.",
        "evidence_refs": [evidence_ref],
        "memory_refs": [],
        "catalysts": [],
        "risks": ["Demand may weaken."],
        "invalidation_conditions": ["New evidence contradicts the thesis."],
        "unresolved_questions": [],
        "time_horizon": "6-12 months",
        "scenarios": [
            {
                "kind": kind,
                "core_assumptions": ["Current evidence remains representative."],
                "outcome": f"The {kind} outcome materializes.",
                "evidence_refs": [evidence_ref],
                "valuation_range": None,
            }
            for kind in ("base", "bull", "bear")
        ],
        "valuation_assessment": None,
        "market_reference_levels": [],
        "calculation_records": [],
        "risk_review_adjustments": [],
    }


@pytest.mark.parametrize(
    ("field", "value", "expected_reason", "expected_issue"),
    (
        ("rating", "StrongBuy", "schema_validation", None),
        ("risks", [], "schema_validation", None),
        ("invalidation_conditions", [], "schema_validation", None),
        (
            "evidence_refs",
            ["ev_ffffffffffff"],
            "semantic_validation",
            "semantic.refs.invalid",
        ),
        (
            "memory_refs",
            ["memory:invented"],
            "semantic_validation",
            "semantic.refs.invalid",
        ),
        (
            "time_horizon",
            "Unspecified",
            "semantic_validation",
            "semantic.text.fallback_sentinel",
        ),
        (
            "thesis",
            '{"rating":"Overweight","confidence":0.4}',
            "semantic_validation",
            "semantic.text.empty_or_nested_json",
        ),
    ),
)
def test_invalid_decision_contract_fails_after_one_recovery(
    field: str,
    value: Any,
    expected_reason: str,
    expected_issue: str | None,
) -> None:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Fixture evidence.",
    )
    bundle = EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
    )
    payload = _decision_payload(item.ref)
    payload[field] = value
    if field == "evidence_refs":
        for scenario in payload["scenarios"]:
            scenario["evidence_refs"] = value
    response = {"raw": AIMessage(content=""), "parsed": payload}
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(StructuredOutputError) as error:
        invoke_research_decision(
            llm,
            prompt="Produce a decision.",
            state={
                "evidence_bundle": bundle.model_dump(mode="json"),
                "risk_reviews": {},
            },
            node="committee.final",
            memory=MemoryContext(instrument="NVDA"),
            require_risk_adjustments=False,
        )

    assert error.value.reason_code == expected_reason
    if expected_issue is not None:
        assert error.value.validation_issues == (expected_issue,)
    assert [method for method, _prompt in llm.calls] == [
        "tool_call",
        "tool_call",
    ]
