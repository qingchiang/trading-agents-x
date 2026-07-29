"""Small typed fixtures shared by application and Web tests."""

from __future__ import annotations

from typing import Literal

from tradingagents.application.contracts import (
    AnalystClaim,
    AnalystClaimType,
    AnalystReport,
    AnalystSection,
    ResearchWarning,
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
