"""Small typed fixtures shared by application and Web tests."""

from __future__ import annotations

from typing import Literal

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    ResearchCase,
    ResearchCaseArgument,
    ResearchDecision,
    ResearchRating,
    ResearchScenario,
    ResearchScenarioKind,
    ResearchWarning,
    RiskReviewAdjustment,
)

_DEFAULT_REF = "ev_0123456789ab"


def analyst_report(
    *,
    analyst: Literal["market", "social", "news", "fundamentals"] = "market",
    evidence_ref: str = _DEFAULT_REF,
    executive_summary: str = "Fixture executive summary.",
    narrative: str = "Fixture evidence-grounded analysis.",
    confidence: float = 0.7,
    warnings: tuple[ResearchWarning | str, ...] = (),
) -> AnalystReport:
    """Return the smallest complete V2 analyst report fixture."""

    return AnalystReport(
        analyst=analyst,
        executive_summary=executive_summary,
        confidence=confidence,
        claims=(
            AnalystClaim(
                id=f"{analyst}.claim_1",
                kind=AnalystClaimType.INFERENCE,
                statement="Fixture evidence supports the stated observation.",
                implication="The committee should preserve this condition.",
                confidence=confidence,
                evidence_refs=(evidence_ref,),
            ),
        ),
        sections=(
            AnalystSection(
                id="overview",
                title="Overview",
                narrative=narrative,
            ),
        ),
        risks=("Fixture evidence may deteriorate.",),
        invalidation_conditions=(
            "New evidence directly contradicts the fixture.",
        ),
        evidence_refs=(evidence_ref,),
        warnings=warnings,
    )


def research_decision(
    *,
    rating: ResearchRating = ResearchRating.HOLD,
    confidence: float = 0.6,
    executive_summary: str = "Fixture decision summary.",
    thesis: str = "Fixture evidence supports a conditional conclusion.",
    evidence_refs: tuple[str, ...] = (_DEFAULT_REF,),
    memory_refs: tuple[str, ...] = (),
    catalysts: tuple[str, ...] = (),
    risks: tuple[str, ...] = ("Fixture downside risk.",),
    invalidation_conditions: tuple[str, ...] = (
        "New evidence contradicts the fixture thesis.",
    ),
    unresolved_questions: tuple[str, ...] = (),
    time_horizon: str = "6-12 months",
    risk_review_adjustments: tuple[RiskReviewAdjustment, ...] = (),
) -> ResearchDecision:
    """Return a complete V2 research-decision fixture."""

    return ResearchDecision(
        rating=rating,
        confidence=confidence,
        executive_summary=executive_summary,
        thesis=thesis,
        evidence_refs=evidence_refs,
        memory_refs=memory_refs,
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
    claim_id: str = "market.claim_1",
) -> ResearchCase:
    """Return a complete claim-driven research-case fixture."""

    return ResearchCase(
        role=role,
        executive_summary=f"Fixture {role} case summary.",
        thesis=f"Fixture {role} thesis.",
        arguments=(
            ResearchCaseArgument(
                id=f"case.{role}.argument_1",
                claim_ids=(claim_id,),
                statement="Fixture case statement.",
                mechanism="Fixture causal mechanism.",
                implication="Fixture decision implication.",
                confidence=0.6,
                evidence_refs=(evidence_ref,),
            ),
        ),
        strongest_counterarguments=(
            "The opposing interpretation remains plausible.",
        ),
        fragile_assumptions=("The fixture mechanism remains valid.",),
        risks=("Fixture evidence may deteriorate.",),
        evidence_refs=(evidence_ref,),
    )
