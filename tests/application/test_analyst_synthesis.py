from __future__ import annotations

from datetime import date
from typing import Any

from tradingagents.application.contracts import (
    AnalystClaimType,
    ClaimImportance,
    EvidenceBundle,
    EvidenceItem,
    KeyClaim,
    ReportAuditStatus,
)
from tradingagents.graph.analyst_synthesis import (
    AnalystAuditDraft,
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
    def __init__(self, value: Any) -> None:
        self.value = value

    def invoke(self, prompt: str, config: Any = None) -> dict[str, Any]:
        del prompt, config
        if isinstance(self.value, Exception):
            raise self.value
        return {"raw": None, "parsed": self.value}


class _AuditLLM:
    preferred_structured_output_method = "function_calling"

    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> _StructuredInvoker:
        return _StructuredInvoker(self.values.pop(0))


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
            KeyClaim(
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

    result = invoke_analyst_report(
        _MarkdownLLM([_Message(markdown)]),
        _AuditLLM([audit]),
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
    assert result.value.key_claims == audit.key_claims
    assert result.value.source_refs == (ref,)


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
            KeyClaim(
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
