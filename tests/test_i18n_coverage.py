"""Every report-producing agent must apply the configured output language
(#740/#801).

A non-English run should produce a fully localized report, not a mix of
languages. The bug originally happened because several agents silently omitted
the instruction (fixed in 6b384f7); this test codifies the invariant so a future
refactor can't quietly drop it again.
"""
from datetime import date

import pytest

from tests.factories import analyst_report
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.research.prompts.language import get_language_instruction
from tradingagents.research.synthesis.role_context import RoleContextBuilder


@pytest.mark.unit
@pytest.mark.parametrize(("language", "scope", "expected"), [
    ("English", "your entire response", ""),
    ("中文", "your entire response", " Write your entire response in 中文."),
    ("Chinese", "all explanatory prose", " Write all explanatory prose in Chinese."),
])
def test_language_instruction(language, scope, expected):
    assert get_language_instruction(language, scope) == expected


@pytest.mark.unit
@pytest.mark.parametrize("role", ["market", "news", "fundamentals", "sentiment"])
def test_report_agent_applies_explicit_runtime_language(monkeypatch, role):
    from tests.factories import captured_analyst_prompt

    prompt = captured_analyst_prompt(monkeypatch, role, language="简体中文")
    assert "in Simplified Chinese (简体中文, zh-CN)." in prompt


@pytest.mark.unit
def test_research_graph_applies_run_language_to_generic_roles():
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="language fixture",
        requested_date=date(2026, 7, 31),
        effective_date=date(2026, 7, 31),
        content="Language routing evidence.",
    )
    bundle = EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 31),
        items=(item,),
    )
    report = analyst_report(
        analyst="market",
        evidence_ref=item.ref,
        narrative="Language routing report.",
    )
    custom_language = (
        "Use concise Simplified Chinese headings and preserve Japanese names."
    )
    context = RoleContextBuilder(
        {
            "evidence_bundle": bundle.model_dump(mode="json"),
            "analyst_reports": {
                "market": report.model_dump(mode="json"),
            },
            "output_language": custom_language,
            "profile": "standard",
        }
    ).build(
        title="Bull Researcher",
        objective="Build the constructive case.",
        stage="opening_case",
        report_mode="full",
    )

    assert custom_language in context.shared_prefix
    assert custom_language in context.prompt
