"""Small typed fixtures shared by application and Web tests."""

from __future__ import annotations

from typing import Literal

from tradingagents.domain.common import (
    ResearchConfidenceLevel,
    ResearchRating,
    ResearchScenarioKind,
)
from tradingagents.domain.decision import ResearchDecision, ResearchScenario, RiskReviewAdjustment
from tradingagents.domain.reports import (
    AnalystClaimType,
    AnalystReport,
    ClaimImportance,
    KeyClaim,
    ReportAuditStatus,
    ReportSection,
    ResearchCase,
    ResearchWarning,
)

_DEFAULT_REF = "ev_0123456789ab"


def analyst_report(
    *,
    analyst: Literal["market", "social", "news", "fundamentals"] = "market",
    evidence_ref: str = _DEFAULT_REF,
    executive_summary: str | None = None,
    narrative: str = "Fixture evidence-grounded analysis.",
    confidence: float = 0.7,
    warnings: tuple[ResearchWarning | str, ...] = (),
) -> AnalystReport:
    """Return the smallest complete Markdown-first analyst report fixture."""

    return AnalystReport(
        analyst=analyst,
        markdown=(
            "# Overview\n\n"
            + (
                f"{executive_summary}\n\n"
                if executive_summary is not None
                else ""
            )
            + narrative
            + f"\n\n[^{evidence_ref}]"
        ),
        report_sections=(
            ReportSection(
                id=f"{analyst}.section.overview",
                title="Overview",
                anchor="overview",
                source_refs=(evidence_ref,),
            ),
        ),
        confidence=confidence,
        key_claims=(
            KeyClaim(
                id=f"{analyst}.claim_1",
                section_id=f"{analyst}.section.overview",
                kind=AnalystClaimType.INFERENCE,
                importance=ClaimImportance.PRIMARY,
                statement="Fixture evidence supports the stated observation.",
                implication="The committee should preserve this condition.",
                confidence=confidence,
                evidence_refs=(evidence_ref,),
            ),
        ),
        source_refs=(evidence_ref,),
        audit_status=ReportAuditStatus.COMPLETE,
        warnings=warnings,
    )

def research_decision(
    *,
    rating: ResearchRating = ResearchRating.HOLD,
    confidence: ResearchConfidenceLevel | str = ResearchConfidenceLevel.MEDIUM,
    executive_summary: str = "Fixture decision summary.",
    thesis: str = "Fixture evidence supports a conditional conclusion.",
    evidence_refs: tuple[str, ...] = (_DEFAULT_REF,),
    catalysts: tuple[str, ...] = (),
    risks: tuple[str, ...] = ("Fixture downside risk.",),
    invalidation_conditions: tuple[str, ...] = (
        "New evidence contradicts the fixture thesis.",
    ),
    unresolved_questions: tuple[str, ...] = (),
    time_horizon: str = "6-12 months",
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = (),
) -> ResearchDecision:
    """Return a complete research-decision fixture."""

    return ResearchDecision(
        rating=rating,
        confidence=confidence,
        executive_summary=executive_summary,
        thesis=thesis,
        evidence_refs=evidence_refs,
        catalysts=catalysts,
        risks=risks,
        invalidation_conditions=invalidation_conditions,
        unresolved_questions=unresolved_questions,
        time_horizon=time_horizon,
        scenarios=tuple(
            ResearchScenario(
                kind=kind,
                core_assumptions=("Fixture assumptions remain valid.",),
                outcome=f"Fixture {kind.value} scenario outcome.",
                evidence_refs=evidence_refs,
            )
            for kind in ResearchScenarioKind
        ),
        risk_review_adjustments=risk_review_adjustments,
    )


def research_case(
    *,
    role: Literal["bull", "bear"] = "bear",
    evidence_ref: str = _DEFAULT_REF,
) -> ResearchCase:
    """Return a shallow Markdown research-case fixture."""

    return ResearchCase(
        role=role,
        markdown=(
            f"# {role.title()} case\n\n"
            f"Fixture case statement grounded in [^{evidence_ref}]."
        ),
    )


def analyst_runtime(config=None):
    """Minimal explicit context for a direct analyst-node test."""
    from copy import deepcopy
    from types import SimpleNamespace

    from langgraph.runtime import Runtime

    from tradingagents.configuration.defaults import DEFAULT_CONFIG

    values = deepcopy(DEFAULT_CONFIG if config is None else config)
    return Runtime(context=SimpleNamespace(
        dataflow_config=values,
        settings=SimpleNamespace(output_language=values["output_language"]),
    ))


def captured_analyst_prompt(monkeypatch, role, *, language="English"):
    """Run an analyst with fixed source inputs and return the submitted prompt."""
    from importlib import import_module
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from tradingagents.configuration.defaults import DEFAULT_CONFIG

    module = import_module(f"tradingagents.research.analysts.{role}_analyst")
    if role == "news":
        monkeypatch.setattr(module, "get_global_macro_panel", lambda *_: "Offline macro input")
    if role == "sentiment":
        monkeypatch.setattr(module, "is_near_live", lambda *_: False)
        monkeypatch.setattr(module.get_news, "func", lambda *_: "Offline news input")
    captured = []

    def invoke(prompt):
        messages = prompt.to_messages() if hasattr(prompt, "to_messages") else prompt
        captured.append("\n".join(str(message.content) for message in messages))
        return AIMessage(content="Offline analyst report")

    model = MagicMock()
    model.bind_tools.return_value = RunnableLambda(invoke)
    model.invoke.side_effect = invoke
    node = getattr(module, f"create_{role}_analyst")(model)
    node({
        "company_of_interest": "NVDA", "trade_date": "2026-01-15", "messages": [],
        "fundamental_inputs": {"responses": {}, "observations": []},
    }, analyst_runtime({**DEFAULT_CONFIG, "output_language": language}))
    return captured[0]
