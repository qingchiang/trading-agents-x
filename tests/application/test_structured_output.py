from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    MemoryContext,
    PerspectiveReview,
)
from tradingagents.graph.research_graph import (
    _invoke_decision,
    _structured_recovery_warnings,
)
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputRunner,
)

_EVIDENCE_REF = "ev_0123456789ab"


def _review(**updates: Any) -> PerspectiveReview:
    values = {
        "role": "bear",
        "thesis": "The evidence supports a skeptical review.",
        "claim_rebuttals": ("The optimistic claim is not yet supported.",),
        "evidence_refs": (_EVIDENCE_REF,),
        "new_evidence_refs": (),
        "risks": ("The downside mechanism may not materialize.",),
    }
    values.update(updates)
    return PerspectiveReview(**values)


def _validate_review(value: PerspectiveReview) -> PerspectiveReview:
    if not value.claim_rebuttals or not value.risks:
        raise ValueError("missing semantic fields")
    if not value.evidence_refs or set(value.evidence_refs) != {_EVIDENCE_REF}:
        raise ValueError("invalid evidence refs")
    return value


class _Invoker:
    def __init__(self, owner: _FakeLLM, method: str, response: Any):
        self.owner = owner
        self.method = method
        self.response = response

    def invoke(self, prompt: str) -> Any:
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
        reject_json_binding: bool = False,
        preferred_method: str = "function_calling",
        structured_output_max_tokens: int | None = None,
    ):
        self.primary = primary
        self.recovery = recovery
        self.plain_recovery = plain_recovery
        self.reject_json_binding = reject_json_binding
        self.preferred_structured_output_method = preferred_method
        self.structured_output_max_tokens = structured_output_max_tokens
        self.calls: list[tuple[str, str]] = []
        self.binds: list[tuple[str, dict[str, Any]]] = []
        self.plain_invoke_kwargs: list[dict[str, Any]] = []

    def with_structured_output(
        self,
        _schema,
        *,
        method: str | None = None,
        include_raw: bool = False,
        **_kwargs,
    ) -> _Invoker:
        assert include_raw is True
        if method == "json_mode" and self.reject_json_binding:
            raise ValueError("json mode unsupported")
        resolved = method or (
            "json_mode"
            if self.preferred_structured_output_method == "json_mode"
            else "tool_call"
        )
        self.binds.append((resolved, dict(_kwargs)))
        response = self.recovery if method == "json_mode" else self.primary
        return _Invoker(self, resolved, response)

    def invoke(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append(("prompt_json", prompt))
        self.plain_invoke_kwargs.append(kwargs)
        if isinstance(self.plain_recovery, BaseException):
            raise self.plain_recovery
        return self.plain_recovery


def _runner(llm: _FakeLLM, events: list[dict[str, Any]]):
    return StructuredOutputRunner(
        llm=llm,
        schema=PerspectiveReview,
        validator=_validate_review,
        node="review.bear",
        event_writer=events.append,
    )


def _invoke(runner):
    return runner.invoke(
        "Produce a bearish review.",
        example=_review().model_dump(mode="json"),
        allowed_evidence_refs=(_EVIDENCE_REF,),
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
    assert [method for method, _ in llm.calls] == ["tool_call"]
    assert llm.calls[0][1] == "Produce a bearish review."
    assert events == []


def test_primary_json_mode_is_a_normal_validated_output() -> None:
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
        recovery=AssertionError("recovery must not run"),
        preferred_method="json_mode",
        structured_output_max_tokens=16_384,
    )

    result = _invoke(_runner(llm, events))

    assert result.value == _review()
    assert result.generation_method is ArtifactGenerationMethod.JSON_MODE
    assert [method for method, _ in llm.calls] == ["json_mode"]
    primary_prompt = llm.calls[0][1]
    assert "Produce the required structured result using JSON Output." in (
        primary_prompt
    )
    assert "Return exactly one JSON object" in primary_prompt
    assert "JSON SCHEMA:" in primary_prompt
    assert "VALID EXAMPLE:" in primary_prompt
    assert '"claim_rebuttals"' in primary_prompt
    assert _EVIDENCE_REF in primary_prompt
    assert "ORIGINAL TASK:\nProduce a bearish review." in primary_prompt
    assert "previous response" not in primary_prompt.casefold()
    assert llm.binds == [("json_mode", {"max_tokens": 16_384})]
    assert _structured_recovery_warnings("review.bear", result) == []
    assert events == []


def test_raw_json_is_recovered_without_another_logical_call() -> None:
    events: list[dict[str, Any]] = []
    raw = json.dumps(_review().model_dump(mode="json"))
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=raw),
            "parsed": None,
            "parsing_error": ValueError("provider parser failed"),
        },
        recovery=AssertionError("recovery must not run"),
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is (
        ArtifactGenerationMethod.RAW_JSON_RECOVERED
    )
    assert [method for method, _ in llm.calls] == ["tool_call"]
    assert [event["event_type"] for event in events] == [
        "node.output_recovered"
    ]


def test_json_mode_recovery_succeeds_with_two_logical_calls() -> None:
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content="not json"),
            "parsed": None,
            "parsing_error": ValueError("not json"),
        },
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is (
        ArtifactGenerationMethod.JSON_MODE_RECOVERED
    )
    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "json_mode",
    ]
    assert [event["event_type"] for event in events] == [
        "node.output_retry",
        "node.output_recovered",
    ]
    recovery_prompt = llm.calls[1][1]
    assert "JSON SCHEMA:" in recovery_prompt
    assert _EVIDENCE_REF in recovery_prompt


def test_truncated_primary_output_retries_with_specific_reason() -> None:
    events: list[dict[str, Any]] = []
    truncated = {
        "raw": AIMessage(
            content=json.dumps(_review().model_dump(mode="json")),
            response_metadata={"finish_reason": "length"},
        ),
        "parsed": _review(),
        "parsing_error": None,
    }
    llm = _FakeLLM(
        primary=truncated,
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is (
        ArtifactGenerationMethod.JSON_MODE_RECOVERED
    )
    assert [event["event_type"] for event in events] == [
        "node.output_retry",
        "node.output_recovered",
    ]
    assert events[0]["payload"]["reason_code"] == "output_truncated"


def test_truncated_recovery_fails_with_specific_reason() -> None:
    events: list[dict[str, Any]] = []
    truncated = {
        "raw": AIMessage(
            content=json.dumps(_review().model_dump(mode="json")),
            response_metadata={"finish_reason": "length"},
        ),
        "parsed": _review(),
        "parsing_error": None,
    }
    llm = _FakeLLM(primary=truncated, recovery=truncated)

    with pytest.raises(StructuredOutputError) as error:
        _invoke(_runner(llm, events))

    assert error.value.reason_code == "output_truncated"
    assert events[-1]["event_type"] == "node.output_failed"
    assert events[-1]["payload"]["reason_code"] == "output_truncated"


def test_prompt_json_recovery_is_strict_when_provider_has_no_json_mode() -> None:
    events: list[dict[str, Any]] = []
    raw = json.dumps(_review().model_dump(mode="json"))
    llm = _FakeLLM(
        primary={"raw": AIMessage(content=""), "parsed": None},
        recovery=None,
        plain_recovery=AIMessage(content=raw),
        reject_json_binding=True,
        structured_output_max_tokens=4096,
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is (
        ArtifactGenerationMethod.JSON_MODE_RECOVERED
    )
    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "prompt_json",
    ]
    assert llm.plain_invoke_kwargs == [{"max_tokens": 4096}]


def test_two_invalid_outputs_fail_without_leaking_provider_content() -> None:
    events: list[dict[str, Any]] = []
    secret = "api_key=must-not-persist"
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=secret),
            "parsed": None,
            "parsing_error": ValueError(secret),
        },
        recovery={
            "raw": AIMessage(content=secret),
            "parsed": None,
            "parsing_error": ValueError(secret),
        },
    )

    with pytest.raises(StructuredOutputError) as error:
        _invoke(_runner(llm, events))

    assert error.value.reason_code == "non_json_response"
    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "json_mode",
    ]
    assert [event["event_type"] for event in events] == [
        "node.output_retry",
        "node.output_failed",
    ]
    assert secret not in str(events)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "invalid",
    (
        _review(claim_rebuttals=()),
        _review(risks=()),
        _review(evidence_refs=("ev_ffffffffffff",)),
    ),
)
def test_semantic_failures_trigger_recovery_then_fail(
    invalid: PerspectiveReview,
) -> None:
    events: list[dict[str, Any]] = []
    response = {"raw": AIMessage(content=""), "parsed": invalid}
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(
        StructuredOutputError,
        match="semantic_validation",
    ):
        _invoke(_runner(llm, events))

    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "json_mode",
    ]


def _decision_payload(evidence_ref: str) -> dict[str, Any]:
    return {
        "rating": "Hold",
        "confidence": 0.5,
        "thesis": "The current evidence supports a balanced conclusion.",
        "evidence_refs": [evidence_ref],
        "memory_refs": [],
        "catalysts": [],
        "risks": ["Demand may weaken."],
        "invalidation_conditions": ["New evidence contradicts the thesis."],
        "time_horizon": "6-12 months",
    }


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    (
        ("rating", "StrongBuy", "schema_validation"),
        ("risks", [], "semantic_validation"),
        ("invalidation_conditions", [], "semantic_validation"),
        ("evidence_refs", ["ev_ffffffffffff"], "semantic_validation"),
        ("memory_refs", ["memory:invented"], "semantic_validation"),
        ("time_horizon", "Unspecified", "semantic_validation"),
        (
            "thesis",
            '{"rating":"Overweight","confidence":0.4}',
            "semantic_validation",
        ),
    ),
)
def test_invalid_decision_contract_fails_after_one_recovery(
    field: str,
    value: Any,
    expected_reason: str,
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
    response = {"raw": AIMessage(content=""), "parsed": payload}
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(StructuredOutputError) as error:
        _invoke_decision(
            llm,
            "Produce a decision.",
            {"evidence_bundle": bundle.model_dump(mode="json")},
            node="committee.final",
            memory=MemoryContext(instrument="NVDA"),
        )

    assert error.value.reason_code == expected_reason
    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "json_mode",
    ]
