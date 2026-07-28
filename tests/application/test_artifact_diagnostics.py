from __future__ import annotations

import json

from tradingagents.application.artifact_diagnostics import diagnose_artifact
from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    PerspectiveReview,
    ResearchDecision,
    ResearchRating,
)


def test_nested_perspective_json_and_missing_fields_are_diagnosed() -> None:
    content = PerspectiveReview(
        role="bear",
        thesis=json.dumps(
            {
                "summary": "Valuation risk is elevated.",
                "downside_mechanisms": ["Multiple compression"],
                "challenged_claims": ["Guidance is not yet validated"],
                "evidence_refs": ["ev_0123456789ab"],
            }
        ),
        evidence_refs=("ev_0123456789ab",),
    )

    diagnostics = diagnose_artifact(
        content,
        ArtifactGenerationMethod.LEGACY_UNKNOWN,
    )

    assert diagnostics.legacy_degraded_output is True
    assert diagnostics.reason_codes == (
        "nested_json_thesis",
        "missing_structured_fields",
    )
    assert diagnostics.missing_fields == ("claim_rebuttals", "risks")
    assert diagnostics.parsed_thesis is not None
    assert diagnostics.parsed_thesis["summary"] == (
        "Valuation risk is elevated."
    )
    assert diagnostics.rerun_recommended is True


def test_nested_decision_conflict_and_fallback_sentinels_are_diagnosed() -> None:
    content = ResearchDecision(
        rating=ResearchRating.HOLD,
        confidence=0.3,
        thesis=json.dumps(
            {
                "rating": "Overweight",
                "confidence": 0.4,
                "thesis": "The parsed legacy thesis.",
                "evidence_refs": ["ev_0123456789ab"],
                "catalysts": ["Order growth"],
                "risks": ["Valuation"],
                "invalidation_conditions": ["Guidance misses"],
                "time_horizon": "6-12 months",
            }
        ),
        evidence_refs=("ev_0123456789ab",),
        risks=("Structured decision output was unavailable.",),
        invalidation_conditions=(
            "Reassess when higher-quality evidence becomes available.",
        ),
        time_horizon="Unspecified research horizon",
    )

    diagnostics = diagnose_artifact(
        content,
        ArtifactGenerationMethod.LEGACY_UNKNOWN,
    )

    assert diagnostics.reason_codes == (
        "nested_json_thesis",
        "fallback_sentinel_fields",
        "rating_conflict",
    )
    assert diagnostics.sentinel_fields == (
        "risks",
        "invalidation_conditions",
        "time_horizon",
    )
    assert diagnostics.outer_rating == "Hold"
    assert diagnostics.nested_rating == "Overweight"
    assert diagnostics.rating_conflict is True


def test_valid_typed_perspective_has_no_degraded_diagnostics() -> None:
    content = PerspectiveReview(
        role="bull",
        thesis="Demand remains constructive.",
        claim_rebuttals=("Valuation is reflected.",),
        evidence_refs=("ev_0123456789ab",),
        risks=("Demand could slow.",),
    )

    diagnostics = diagnose_artifact(
        content,
        ArtifactGenerationMethod.TOOL_CALL,
    )

    assert diagnostics.degraded_output is False
    assert diagnostics.parsed_thesis is None
    assert diagnostics.rerun_recommended is False
