from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from tests.factories import analyst_report
from tradingagents.application.contracts import EvidenceBundle, EvidenceItem
from tradingagents.application.evidence import extract_evidence_tables
from tradingagents.graph.role_context import RoleContextBuilder


def _state(rows: int = 200) -> dict[str, Any]:
    start = date(2024, 1, 2)
    lines = ["Date,Open,High,Low,Close,Volume"]
    for index in range(rows):
        current = start + timedelta(days=index)
        close = 100 + index
        lines.append(
            f"{current.isoformat()},{close - 1},{close + 2},"
            f"{close - 2},{close},{1_000_000 + index}"
        )
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="get_stock_data",
        requested_date=date(2026, 7, 30),
        effective_date=date(2026, 7, 30),
        content="\n".join(lines),
        provenance={
            "analytical_views": {
                "row_count": rows,
                "latest_close": 100 + rows - 1,
            }
        },
    )
    bundle = EvidenceBundle(
        instrument="4568.T",
        analysis_date=date(2026, 7, 30),
        items=(item,),
        tables=extract_evidence_tables((item,)),
    )
    report = analyst_report(
        analyst="market",
        evidence_ref=item.ref,
        narrative="Complete localized report with a compact comparison table.",
    )
    return {
        "ticker": bundle.instrument,
        "analysis_date": bundle.analysis_date.isoformat(),
        "profile": "standard",
        "output_language": "Simplified Chinese (简体中文, zh-CN)",
        "analyst_reports": {"market": report.model_dump(mode="json")},
        "evidence_bundle": bundle.model_dump(mode="json"),
        "cases": {},
        "rebuttals": [],
        "risk_reviews": {},
    }


def test_roles_share_a_byte_identical_stable_prefix() -> None:
    state = _state()
    builder = RoleContextBuilder(state)

    bull = builder.build(
        title="Bull Researcher",
        objective="Build the constructive case.",
        stage="opening_case",
        report_mode="full",
        evidence_refs=builder.primary_evidence_refs(),
    )
    risk = builder.build(
        title="Integrated Risk Reviewer",
        objective="Challenge the judge draft.",
        stage="risk_review",
        artifacts={"judge_draft": {"markdown": "Judge marker."}},
        report_mode="risk",
    )

    assert bull.shared_prefix == risk.shared_prefix
    assert bull.prompt.startswith(bull.shared_prefix)
    assert risk.prompt.startswith(bull.shared_prefix)
    assert bull.prompt.index("Bull Researcher") > len(bull.shared_prefix)
    assert risk.prompt.index("Integrated Risk Reviewer") > len(
        risk.shared_prefix
    )
    assert "Complete localized report" not in bull.shared_prefix
    assert "report_index" not in bull.shared_prefix
    language = state["output_language"]
    assert language in bull.shared_prefix
    assert bull.prompt.count(language) >= 3
    assert risk.prompt.count(language) >= 3


def test_full_report_context_does_not_duplicate_primary_claims() -> None:
    builder = RoleContextBuilder(_state())

    context = builder.build(
        title="Bull Researcher",
        objective="Build the constructive case.",
        stage="opening_case",
        report_mode="full",
        evidence_refs=builder.primary_evidence_refs(),
    )

    assert context.prompt.count(
        "The committee should preserve this condition."
    ) == 1


def test_agenda_context_contains_cases_without_the_evidence_catalog() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    state["cases"] = {
        "bull": {"role": "bull", "markdown": f"Bull case.[^{ref}]"},
        "bear": {"role": "bear", "markdown": f"Bear case.[^{ref}]"},
    }

    context = RoleContextBuilder(state).build_agenda(
        title="Research Debate Moderator",
        objective="Prioritize material disagreements.",
    )

    assert "Bull case." in context.prompt
    assert "Bear case." in context.prompt
    assert '"evidence_ref_whitelist"' in context.prompt
    assert ref in context.prompt
    assert "Fixture evidence supports the stated observation." in context.prompt
    assert "market.claim_1" not in context.prompt
    assert '"evidence_catalog"' not in context.prompt
    assert '"analyst_reports"' not in context.prompt
    assert context.catalog_items == 0
    assert context.catalog_tables == 0


def test_agenda_context_size_does_not_scale_with_unreferenced_evidence() -> None:
    state = _state()
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    ref = bundle.items[0].ref
    cases = {
        "bull": {"role": "bull", "markdown": f"Bull case.[^{ref}]"},
        "bear": {"role": "bear", "markdown": f"Bear case.[^{ref}]"},
    }
    state["cases"] = cases
    expanded_items = bundle.items + tuple(
        EvidenceItem.create(
            source=f"unused-{index}",
            evidence_type="unused fixture",
            requested_date=bundle.analysis_date,
            effective_date=bundle.analysis_date,
            content=f"Unreferenced evidence body {index}." * 20,
        )
        for index in range(60)
    )
    expanded = EvidenceBundle(
        instrument=bundle.instrument,
        analysis_date=bundle.analysis_date,
        items=expanded_items,
        tables=bundle.tables,
    )
    expanded_state = dict(state)
    expanded_state["evidence_bundle"] = expanded.model_dump(mode="json")

    compact = RoleContextBuilder(state).build_agenda(
        title="Research Debate Moderator",
        objective="Prioritize material disagreements.",
    )
    expanded_context = RoleContextBuilder(expanded_state).build_agenda(
        title="Research Debate Moderator",
        objective="Prioritize material disagreements.",
    )

    assert expanded_context.prompt == compact.prompt


def test_final_context_does_not_rebroadcast_case_or_rebuttal_markdown() -> None:
    state = _state()
    state["cases"] = {
        "bull": {"markdown": "OPENING-CASE-UNIQUE-MARKER"}
    }
    state["rebuttals"] = [
        {"markdown": "REBUTTAL-UNIQUE-MARKER"}
    ]
    builder = RoleContextBuilder(state)

    final = builder.build(
        title="Final Research Committee",
        objective="Form the final opinion.",
        stage="final_committee",
        artifacts={
            "judge_draft": {"markdown": "Judge conclusion."},
            "risk_reviews": {
                "integrated": {"markdown": "Risk conclusion."}
            },
        },
        report_mode="full",
        evidence_refs=builder.primary_evidence_refs(),
    )

    assert "Judge conclusion." in final.prompt
    assert "Risk conclusion." in final.prompt
    assert "OPENING-CASE-UNIQUE-MARKER" not in final.prompt
    assert "REBUTTAL-UNIQUE-MARKER" not in final.prompt


def test_role_context_size_does_not_scale_with_raw_market_rows() -> None:
    short_builder = RoleContextBuilder(_state(200))
    long_builder = RoleContextBuilder(_state(600))

    short = short_builder.build(
        title="Bull Researcher",
        objective="Build the constructive case.",
        stage="opening_case",
        report_mode="full",
        evidence_refs=short_builder.primary_evidence_refs(),
    )
    long = long_builder.build(
        title="Bull Researcher",
        objective="Build the constructive case.",
        stage="opening_case",
        report_mode="full",
        evidence_refs=long_builder.primary_evidence_refs(),
    )

    assert "2024-01-02,99,102,98,100,1000000" not in short.prompt
    assert "2024-01-02,99,102,98,100,1000000" not in long.prompt
    assert abs(long.inline_characters - short.inline_characters) < 500
