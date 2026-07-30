"""Small typed fixtures shared by application and Web tests."""

from __future__ import annotations

from typing import Literal

from tradingagents.application.contracts import (
    AnalystClaimType,
    AnalystReport,
    ClaimImportance,
    KeyClaim,
    ReportAuditStatus,
    ReportSection,
    ResearchCase,
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
    """Return a complete research-decision fixture."""

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
    """Return a shallow Markdown research-case fixture."""

    return ResearchCase(
        role=role,
        markdown=(
            f"# {role.title()} case\n\n"
            f"Fixture case statement grounded in [^{evidence_ref}]."
        ),
        focus_claim_ids=(claim_id,),
        report_section_refs=("market.section.overview",),
    )
