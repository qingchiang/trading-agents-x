from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    ArtifactGenerationMethod,
    EvidenceBundle,
    EvidenceItem,
    MemoryContext,
)
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.graph.analyst_synthesis import (
    _ANALYST_SECTIONS,
    _analyst_report_example,
    _AnalystReportManifest,
    _AnalystSectionChunk,
    invoke_analyst_report,
)
from tradingagents.graph.deliberation import invoke_research_decision
from tradingagents.graph.research_graph import (
    _structured_recovery_warnings,
)
from tradingagents.graph.structured_output import (
    StructuredOutputError,
    StructuredOutputRunner,
)

_EVIDENCE_REF = "ev_0123456789ab"


class _ReviewOutput(BaseModel):
    role: str
    thesis: str
    claim_rebuttals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    new_evidence_refs: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()


def _review(**updates: Any) -> _ReviewOutput:
    values = {
        "role": "bear",
        "thesis": "The evidence supports a skeptical review.",
        "claim_rebuttals": ("The optimistic claim is not yet supported.",),
        "evidence_refs": (_EVIDENCE_REF,),
        "new_evidence_refs": (),
        "risks": ("The downside mechanism may not materialize.",),
    }
    values.update(updates)
    return _ReviewOutput(**values)


def _validate_review(value: _ReviewOutput) -> _ReviewOutput:
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
            "json_mode" if self.preferred_structured_output_method == "json_mode" else "tool_call"
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
        schema=_ReviewOutput,
        validator=_validate_review,
        node="case.bear",
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
    assert "Produce the required structured result using JSON Output." in (primary_prompt)
    assert "Return exactly one JSON object" in primary_prompt
    assert "JSON SCHEMA:" in primary_prompt
    assert "VALID EXAMPLE:" in primary_prompt
    assert '"claim_rebuttals"' in primary_prompt
    assert _EVIDENCE_REF in primary_prompt
    assert "ORIGINAL TASK:\nProduce a bearish review." in primary_prompt
    assert "previous response" not in primary_prompt.casefold()
    assert llm.binds == [("json_mode", {"max_tokens": 16_384})]
    assert _structured_recovery_warnings("case.bear", result) == []
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

    assert result.generation_method is (ArtifactGenerationMethod.RAW_JSON_RECOVERED)
    assert [method for method, _ in llm.calls] == ["tool_call"]
    assert [event["event_type"] for event in events] == ["node.output_recovered"]


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

    assert result.generation_method is (ArtifactGenerationMethod.JSON_MODE_RECOVERED)
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


def test_json_recovery_receives_safe_schema_issue_paths() -> None:
    events: list[dict[str, Any]] = []
    invalid = _review().model_dump(mode="json")
    invalid["evidence_refs"] = 123
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=json.dumps(invalid)),
            "parsed": None,
            "parsing_error": None,
        },
        recovery={
            "raw": AIMessage(content=""),
            "parsed": _review(),
            "parsing_error": None,
        },
    )

    result = _invoke(_runner(llm, events))

    assert result.generation_method is (ArtifactGenerationMethod.JSON_MODE_RECOVERED)
    issue = "schema.evidence_refs.tuple_type"
    assert issue in llm.calls[1][1]
    assert events[0]["payload"]["validation_issues"] == [issue]
    assert events[1]["payload"]["validation_issues"] == [issue]


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

    assert result.generation_method is (ArtifactGenerationMethod.JSON_MODE_RECOVERED)
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

    assert result.generation_method is (ArtifactGenerationMethod.JSON_MODE_RECOVERED)
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


def _analyst_bundle(*, with_table: bool = True) -> EvidenceBundle:
    content = (
        "## Verified snapshot\n\n| Metric | Value |\n|---|---:|\n| Revenue | 120 |"
        if with_table
        else "Fixture evidence body."
    )
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="fundamental data",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content=content,
    )
    tables = extract_evidence_tables((item,))
    return EvidenceBundle(
        instrument="NVDA",
        analysis_date=date(2026, 7, 24),
        items=(item,),
        tables=tables,
    )


def test_analyst_report_is_synthesized_from_catalogued_evidence() -> None:
    bundle = _analyst_bundle()
    report = _analyst_report_example(
        analyst="fundamentals",
        bundle=bundle,
        confidence_override=None,
    )
    source_view = report.tables[0]
    first_row = source_view.rows[0]
    source_view = source_view.model_copy(
        update={
            "rows": (
                first_row.model_copy(
                    update={
                        "cells": {
                            **first_row.cells,
                            "value": first_row.cells["value"].model_copy(
                                update={
                                    "display_value": (
                                        "MODEL VALUE MUST NOT SURVIVE"
                                    )
                                }
                            ),
                        }
                    }
                ),
            )
        }
    )
    report = report.model_copy(update={"tables": (source_view,)})
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={
            "raw": AIMessage(content=""),
            "parsed": report,
            "parsing_error": None,
        },
        recovery=AssertionError("recovery must not run"),
    )

    result = invoke_analyst_report(
        llm,
        analyst="fundamentals",
        draft_narrative="A detailed tool-agent draft.",
        bundle=bundle,
        output_language="English (en)",
        confidence_override=None,
        warnings=(),
        node="analyst.fundamentals",
        event_writer=events.append,
    )

    assert result.generation_method is ArtifactGenerationMethod.TOOL_CALL
    assert result.value.sections[0].source_table_ids == (bundle.tables[0].id,)
    assert result.value.sections[0].table_ids == ("rt_fundamentals_source_view",)
    assert (
        result.value.tables[0].rows[0].cells["value"].display_value
        == "120"
    )
    assert '"row_count": 1' in llm.calls[0][1]
    assert '"Revenue"' not in llm.calls[0][1]
    assert "EVIDENCE CATALOG" in llm.calls[0][1]
    assert bundle.items[0].ref in llm.calls[0][1]
    assert "There is no" in llm.calls[0][1]
    assert events == []


def test_analyst_report_normalizes_redundant_top_level_refs() -> None:
    base = _analyst_bundle()
    second = EvidenceItem.create(
        source="second fixture",
        evidence_type="fundamental detail",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        content="Second evidence body.",
    )
    bundle = EvidenceBundle(
        instrument=base.instrument,
        analysis_date=base.analysis_date,
        items=(*base.items, second),
        tables=base.tables,
    )
    report = _analyst_report_example(
        analyst="fundamentals",
        bundle=bundle,
        confidence_override=None,
    )
    claim = report.claims[0].model_copy(update={"evidence_refs": (second.ref,)})
    incomplete_index = report.model_copy(
        update={
            "claims": (claim,),
            "evidence_refs": (base.items[0].ref,),
        }
    )
    llm = _FakeLLM(
        primary={"raw": AIMessage(content=""), "parsed": incomplete_index},
        recovery=AssertionError("recovery must not run"),
    )

    result = invoke_analyst_report(
        llm,
        analyst="fundamentals",
        draft_narrative="Detailed draft.",
        bundle=bundle,
        output_language="English (en)",
        confidence_override=None,
        warnings=(),
        node="analyst.fundamentals",
    )

    assert result.value.evidence_refs == (
        base.items[0].ref,
        second.ref,
    )
    assert [method for method, _prompt in llm.calls] == ["tool_call"]


def test_sentiment_confidence_uses_deterministic_override() -> None:
    bundle = _analyst_bundle(with_table=False)
    report = _analyst_report_example(
        analyst="social",
        bundle=bundle,
        confidence_override=0.55,
    ).model_copy(update={"confidence": 0.9})
    llm = _FakeLLM(
        primary={"raw": AIMessage(content=""), "parsed": report},
        recovery=AssertionError("recovery must not run"),
    )

    result = invoke_analyst_report(
        llm,
        analyst="social",
        draft_narrative="Detailed sentiment draft.",
        bundle=bundle,
        output_language="Simplified Chinese (简体中文, zh-CN)",
        confidence_override=0.55,
        warnings=(),
        node="analyst.social",
    )

    assert result.value.confidence == 0.55
    assert "Set `confidence` exactly to 0.55" in llm.calls[0][1]
    assert [method for method, _prompt in llm.calls] == ["tool_call"]


def test_analyst_semantics_reject_missing_sections_and_fabricated_refs() -> None:
    bundle = _analyst_bundle()
    report = _analyst_report_example(
        analyst="market",
        bundle=bundle,
        confidence_override=None,
    )
    invalid_claim = report.claims[0].model_copy(update={"evidence_refs": ("ev_ffffffffffff",)})
    invalid = report.model_copy(
        update={
            "claims": (invalid_claim,),
            "sections": report.sections[:1],
        }
    )
    response = {"raw": AIMessage(content=""), "parsed": invalid}
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(
        StructuredOutputError,
        match="semantic_validation",
    ) as error:
        invoke_analyst_report(
            llm,
            analyst="market",
            draft_narrative="Draft.",
            bundle=bundle,
            output_language="English (en)",
            confidence_override=None,
            warnings=(),
            node="analyst.market",
        )

    issue = "semantic.analyst.sections.required"
    assert error.value.validation_issues == (issue,)
    assert issue in llm.calls[1][1]


def test_analyst_rejects_source_table_value_mismatch() -> None:
    bundle = _analyst_bundle()
    report = _analyst_report_example(
        analyst="market",
        bundle=bundle,
        confidence_override=None,
    )
    table = report.tables[0]
    row = table.rows[0]
    mismatched = row.cells["value"].model_copy(
        update={"raw_value": 999}
    )
    invalid = report.model_copy(
        update={
            "tables": (
                table.model_copy(
                    update={
                        "rows": (
                            row.model_copy(
                                update={
                                    "cells": {
                                        **row.cells,
                                        "value": mismatched,
                                    }
                                }
                            ),
                        )
                    }
                ),
            )
        }
    )
    response = {"raw": AIMessage(content=""), "parsed": invalid}
    llm = _FakeLLM(primary=response, recovery=response)

    with pytest.raises(StructuredOutputError) as error:
        invoke_analyst_report(
            llm,
            analyst="market",
            draft_narrative="Draft.",
            bundle=bundle,
            output_language="English (en)",
            confidence_override=None,
            warnings=(),
            node="analyst.market",
        )

    assert error.value.validation_issues == (
        "semantic.research_table.source.value_mismatch",
    )


def test_analyst_semantic_hint_guides_successful_recovery() -> None:
    bundle = _analyst_bundle()
    report = _analyst_report_example(
        analyst="fundamentals",
        bundle=bundle,
        confidence_override=None,
    )
    incomplete = report.model_copy(update={"sections": report.sections[:1]})
    events: list[dict[str, Any]] = []
    llm = _FakeLLM(
        primary={"raw": AIMessage(content=""), "parsed": incomplete},
        recovery={"raw": AIMessage(content=""), "parsed": report},
    )

    result = invoke_analyst_report(
        llm,
        analyst="fundamentals",
        draft_narrative="Detailed draft.",
        bundle=bundle,
        output_language="Simplified Chinese (简体中文, zh-CN)",
        confidence_override=None,
        warnings=(),
        node="analyst.fundamentals",
        event_writer=events.append,
    )

    issue = "semantic.analyst.sections.required"
    assert result.value == report
    assert issue in llm.calls[1][1]
    assert events[0]["payload"]["validation_issues"] == [issue]
    assert events[1]["payload"]["validation_issues"] == [issue]


class _SectionedInvoker:
    def __init__(self, owner: _SectionedLLM, schema: type[BaseModel]):
        self.owner = owner
        self.schema = schema

    def invoke(self, prompt: str) -> dict[str, Any]:
        self.owner.calls.append((self.schema.__name__, prompt))
        ref = self.owner.ref
        if self.schema is AnalystReport:
            return {
                "raw": AIMessage(
                    content="{",
                    response_metadata={"finish_reason": "length"},
                ),
                "parsed": None,
            }
        if self.schema is _AnalystReportManifest:
            return {
                "raw": AIMessage(content=""),
                "parsed": _AnalystReportManifest(
                    analyst="market",
                    executive_summary="Complete sectioned summary.",
                    confidence=0.65,
                    claims=(
                        AnalystClaim(
                            id="market.claim_1",
                            kind=AnalystClaimType.INFERENCE,
                            statement="Evidence supports a mixed regime.",
                            implication="Maintain conditional conclusions.",
                            confidence=0.65,
                            evidence_refs=(ref,),
                        ),
                    ),
                    sections=tuple(
                        {
                            "id": section_id,
                            "title": title,
                            "source_table_ids": (),
                        }
                        for section_id, title in _ANALYST_SECTIONS["market"]
                    ),
                    risks=("The observed regime may reverse.",),
                    invalidation_conditions=("New evidence contradicts the regime.",),
                    evidence_refs=(ref,),
                ),
            }
        if self.schema is _AnalystSectionChunk:
            match = re.search(r"Generate only section `([^`]+)`", prompt)
            assert match is not None
            section_id = match.group(1)
            title = dict(_ANALYST_SECTIONS["market"])[section_id]
            return {
                "raw": AIMessage(content=""),
                "parsed": _AnalystSectionChunk(
                    section=AnalystSection(
                        id=section_id,
                        title=title,
                        narrative=(
                            f"Complete detailed analysis for {section_id} grounded in {ref}."
                        ),
                    ),
                ),
            }
        raise AssertionError(self.schema)


class _SectionedLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, ref: str):
        self.ref = ref
        self.calls: list[tuple[str, str]] = []

    def with_structured_output(
        self,
        schema,
        *,
        include_raw: bool,
        **_kwargs,
    ) -> _SectionedInvoker:
        assert include_raw is True
        return _SectionedInvoker(self, schema)


def test_truncated_analyst_output_recovers_by_manifest_and_sections() -> None:
    bundle = _analyst_bundle(with_table=False)
    llm = _SectionedLLM(bundle.items[0].ref)
    events: list[dict[str, Any]] = []

    result = invoke_analyst_report(
        llm,
        analyst="market",
        draft_narrative="Full draft.",
        bundle=bundle,
        output_language="English (en)",
        confidence_override=None,
        warnings=(),
        node="analyst.market",
        event_writer=events.append,
    )

    assert result.generation_method is (ArtifactGenerationMethod.SECTIONED_RECOVERY)
    assert len(result.value.sections) == len(_ANALYST_SECTIONS["market"])
    assert all(
        section.narrative.startswith("Complete detailed analysis")
        for section in result.value.sections
    )
    assert [schema for schema, _prompt in llm.calls] == [
        "AnalystReport",
        "_AnalystReportManifest",
        *["_AnalystSectionChunk" for _section in _ANALYST_SECTIONS["market"]],
    ]
    assert events[0]["event_type"] == "node.output_retry"
    assert events[0]["payload"]["method"] == "sectioned_recovery"
    assert events[-1]["event_type"] == "node.output_recovered"


@pytest.mark.parametrize(
    "invalid",
    (
        _review(claim_rebuttals=()),
        _review(risks=()),
        _review(evidence_refs=("ev_ffffffffffff",)),
    ),
)
def test_semantic_failures_trigger_recovery_then_fail(
    invalid: _ReviewOutput,
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
        "risk_review_adjustments": [],
    }


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    (
        ("rating", "StrongBuy", "schema_validation"),
        ("risks", [], "schema_validation"),
        ("invalidation_conditions", [], "schema_validation"),
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
    assert [method for method, _ in llm.calls] == [
        "tool_call",
        "json_mode",
    ]
