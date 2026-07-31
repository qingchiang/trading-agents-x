"""Every report-producing agent must apply the configured output language
(#740/#801).

A non-English run should produce a fully localized report, not a mix of
languages. The bug originally happened because several agents silently omitted
the instruction (fixed in 6b384f7); this test codifies the invariant so a future
refactor can't quietly drop it again.
"""
from datetime import date
from pathlib import Path

import pytest

from tests.factories import analyst_report
from tradingagents.agents.utils.agent_utils import get_language_instruction
from tradingagents.application.contracts import EvidenceBundle, EvidenceItem
from tradingagents.graph.role_context import RoleContextBuilder

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "tradingagents" / "agents"

# Every node whose text reaches the saved report. If you add a report-producing
# agent, add it here — and make it call get_language_instruction().
REPORT_AGENTS = [
    "analysts/market_analyst.py",
    "analysts/news_analyst.py",
    "analysts/fundamentals_analyst.py",
    "analysts/sentiment_analyst.py",
]


@pytest.mark.unit
class TestLanguageInstruction:
    def test_english_adds_no_tokens(self, monkeypatch):
        from tradingagents.dataflows.config import bind_config
        bind_config({"output_language": "English"})
        assert get_language_instruction() == ""

    def test_non_english_emits_directive(self):
        from tradingagents.dataflows.config import bind_config
        bind_config({"output_language": "中文"})
        out = get_language_instruction()
        assert "中文" in out
        assert "entire response" in out

    def test_custom_scope_limits_directive(self):
        from tradingagents.dataflows.config import bind_config
        bind_config({"output_language": "Chinese"})
        out = get_language_instruction("all explanatory prose")
        assert out == " Write all explanatory prose in Chinese."


@pytest.mark.unit
@pytest.mark.parametrize("rel", REPORT_AGENTS)
def test_report_agent_applies_language_instruction(rel):
    path = _AGENTS_DIR / rel
    assert path.exists(), f"missing agent module: {rel}"
    src = path.read_text(encoding="utf-8")
    assert "get_language_instruction(" in src, (
        f"{rel} does not apply get_language_instruction(); its output would "
        f"ignore the configured output_language (#740/#801)."
    )


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
