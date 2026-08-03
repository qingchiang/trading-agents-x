from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from tradingagents.application.contracts import (
    AnalystClaimType,
    ClaimImportance,
    EvidenceBundle,
    EvidenceItem,
    ReportAuditStatus,
)
from tradingagents.graph.analyst_synthesis import (
    AnalystAuditDraft,
    AuditKeyClaimDraft,
    invoke_analyst_report,
    normalize_report_citations,
)


class _Message:
    def __init__(
        self,
        content: str,
        *,
        finish_reason: str = "stop",
    ) -> None:
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}


class _MarkdownLLM:
    def __init__(self, responses: list[_Message]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def invoke(self, prompt: str, config: Any = None) -> _Message:
        del config
        self.prompts.append(prompt)
        return self.responses.pop(0)


class _StructuredInvoker:
    def __init__(self, value: Any, prompts: list[str]) -> None:
        self.value = value
        self.prompts = prompts

    def invoke(self, prompt: str, config: Any = None) -> dict[str, Any]:
        del config
        self.prompts.append(prompt)
        if isinstance(self.value, Exception):
            raise self.value
        return {"raw": None, "parsed": self.value}


class _AuditLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.prompts: list[str] = []

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> _StructuredInvoker:
        return _StructuredInvoker(self.values.pop(0), self.prompts)


def _bundle() -> EvidenceBundle:
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market snapshot",
        requested_date=date(2026, 7, 30),
        effective_date=date(2026, 7, 30),
        content="Revenue improved while cash conversion weakened.",
    )
    return EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 30),
        items=(item,),
    )


def test_markdown_report_preserves_tables_and_extracts_small_audit() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    markdown = (
        "# 核心观察\n\n"
        "| 指标 | 本期 | 前期 |\n"
        "|---|---:|---:|\n"
        "| 收入 | 120亿元 | 100亿元 |\n\n"
        f"收入改善，但现金转化仍需观察。[^{ref}]"
    )
    audit = AnalystAuditDraft(
        confidence=0.7,
        key_claims=(
            AuditKeyClaimDraft(
                id="fundamentals.claim_1",
                section_id="fundamentals.section_1",
                kind=AnalystClaimType.INFERENCE,
                importance=ClaimImportance.PRIMARY,
                statement="收入改善但现金转化偏弱。",
                implication="结论需要保留质量折扣。",
                confidence=0.7,
                evidence_refs=(ref,),
            ),
        ),
        section_source_refs={"fundamentals.section_1": (ref,)},
    )

    audit_llm = _AuditLLM([audit])
    result = invoke_analyst_report(
        _MarkdownLLM([_Message(markdown)]),
        audit_llm,
        analyst="fundamentals",
        draft_narrative="Use the sealed evidence.",
        bundle=bundle,
        output_language="zh-CN",
        prepared_evidence=None,
        confidence_override=None,
        warnings=(),
        node="analyst.fundamentals",
    )

    assert "| 收入 | 120亿元 | 100亿元 |" in result.value.markdown
    assert result.value.audit_status is ReportAuditStatus.COMPLETE
    assert [claim.model_dump() for claim in result.value.key_claims] == [
        claim.model_dump() for claim in audit.key_claims
    ]
    assert result.value.source_refs == (ref,)
    assert "zh-CN" in audit_llm.prompts[0]
    assert "报告中的一项决策相关观点" in audit_llm.prompts[0]


def test_analyst_audit_schema_requires_cited_claims_and_primary_claim() -> None:
    schema = AnalystAuditDraft.model_json_schema()
    claim_schema = schema["$defs"]["AuditKeyClaimDraft"]

    assert schema["properties"]["key_claims"]["minItems"] == 1
    assert claim_schema["properties"]["evidence_refs"]["minItems"] == 1

    with pytest.raises(ValidationError, match="at least 1 item"):
        AuditKeyClaimDraft(
            id="fundamentals.claim_1",
            section_id="fundamentals.section_1",
            kind=AnalystClaimType.INFERENCE,
            importance=ClaimImportance.PRIMARY,
            statement="收入增长。",
            implication="支持增长判断。",
            confidence=0.7,
            evidence_refs=(),
        )


def test_custom_output_language_reaches_audit_primary_and_repair_prompts() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    custom_language = "Use formal Chinese prose with Japanese company names unchanged."
    valid = AnalystAuditDraft(
        confidence=0.7,
        key_claims=(
            AuditKeyClaimDraft(
                id="fundamentals.claim_1",
                section_id="fundamentals.section_1",
                kind=AnalystClaimType.INFERENCE,
                importance=ClaimImportance.PRIMARY,
                statement="收入改善。",
                implication="支持增长判断。",
                confidence=0.7,
                evidence_refs=(ref,),
            ),
        ),
    )
    audit_llm = _AuditLLM([RuntimeError("schema failure"), valid])

    result = invoke_analyst_report(
        _MarkdownLLM([_Message(f"# 核心观察\n\n收入改善。[^{ref}]")]),
        audit_llm,
        analyst="fundamentals",
        draft_narrative="Use the sealed evidence.",
        bundle=bundle,
        output_language=custom_language,
        prepared_evidence=None,
        confidence_override=None,
        warnings=(),
        node="analyst.fundamentals",
    )

    assert result.value.audit_status is ReportAuditStatus.COMPLETE
    assert len(audit_llm.prompts) == 2
    assert all(custom_language in prompt for prompt in audit_llm.prompts)


def test_unknown_citation_is_removed_without_discarding_report() -> None:
    bundle = _bundle()
    markdown = "# Overview\n\nSupported.[^ev_ffffffffffff] Unknown.[^ev_deadbeefdead]"

    normalized, _sections, refs, warnings = normalize_report_citations(
        markdown,
        bundle=bundle,
        analyst="market",
    )

    assert "ev_ffffffffffff" not in normalized
    assert "ev_deadbeefdead" not in normalized
    assert refs == ()
    assert {warning.code for warning in warnings} == {
        "report.unknown_evidence_ref"
    }


def test_report_citation_normalization_drops_model_source_definitions() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    normalized, _sections, refs, warnings = normalize_report_citations(
        (
            f"# Overview\n\nSupported.[^{ref}]\n\n"
            f"[^{ref}]: A model-authored source description.\n"
        ),
        bundle=bundle,
        analyst="market",
    )

    assert normalized == f"# Overview\n\nSupported.[^{ref}]"
    assert refs == (ref,)
    assert warnings == ()


def test_failed_audit_keeps_markdown_and_marks_incomplete() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    writer = _MarkdownLLM(
        [_Message(f"# Overview\n\nReadable evidence-grounded report.[^{ref}]")]
    )
    audit_llm = _AuditLLM([RuntimeError("bad audit"), RuntimeError("bad repair")])

    result = invoke_analyst_report(
        writer,
        audit_llm,
        analyst="market",
        draft_narrative="Use the sealed evidence.",
        bundle=bundle,
        output_language="en",
        prepared_evidence=None,
        confidence_override=None,
        warnings=(),
        node="analyst.market",
    )

    assert result.value.audit_status is ReportAuditStatus.INCOMPLETE
    assert result.value.key_claims == ()
    assert result.value.source_refs == (ref,)
    assert any(
        warning.code == "report.audit_incomplete"
        for warning in result.value.warnings
    )


def test_report_without_evidence_skips_audit_and_remains_readable() -> None:
    bundle = EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 30),
        items=(),
    )
    result = invoke_analyst_report(
        _MarkdownLLM([_Message("# Overview\n\nNo evidence was available.")]),
        _AuditLLM([]),
        analyst="market",
        draft_narrative="State the evidence limitation.",
        bundle=bundle,
        output_language="en",
        prepared_evidence=None,
        confidence_override=None,
        warnings=(),
        node="analyst.market",
    )

    assert "No evidence was available." in result.value.markdown
    assert result.value.audit_status is ReportAuditStatus.INCOMPLETE
    assert result.value.source_refs == ()


def test_truncated_markdown_gets_one_continuation() -> None:
    bundle = _bundle()
    ref = bundle.items[0].ref
    writer = _MarkdownLLM(
        [
            _Message("# Overview\n\nFirst block.", finish_reason="length"),
            _Message(f"\n\n## Risks\n\nSecond block.[^{ref}]"),
        ]
    )
    audit = AnalystAuditDraft(
        confidence=0.6,
        key_claims=(
            AuditKeyClaimDraft(
                id="market.claim_1",
                section_id="market.section_1",
                kind=AnalystClaimType.INFERENCE,
                importance=ClaimImportance.PRIMARY,
                statement="The evidence is mixed.",
                implication="Keep the conclusion conditional.",
                confidence=0.6,
                evidence_refs=(ref,),
            ),
        ),
        section_source_refs={"market.section_1": (ref,)},
    )

    result = invoke_analyst_report(
        writer,
        _AuditLLM([audit]),
        analyst="market",
        draft_narrative="Use the sealed evidence.",
        bundle=bundle,
        output_language="en",
        prepared_evidence=None,
        confidence_override=None,
        warnings=(),
        node="analyst.market",
    )

    assert len(writer.prompts) == 2
    assert "First block." in result.value.markdown
    assert "Second block." in result.value.markdown
