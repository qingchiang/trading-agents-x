from __future__ import annotations

from datetime import date, datetime

import pytest

from tradingagents.application.outcome_feedback import (
    HORIZON_LIMIT,
    METHOD_CATEGORY,
    QUALIFICATION_POLICY_VERSION,
    FeedbackSource,
    ObservationQualificationInput,
    OutcomeFeedbackStatus,
    ReflectionQualificationInput,
    qualify_reflection,
)


def _qualify(reflection: str, **updates):
    values = dict(updates)
    source = FeedbackSource(
        decision_id=values.pop("decision_id", 7),
        revision_id=values.pop("revision_id", "revision-1"),
        decision_rating=values.pop("decision_rating", "Hold"),
        decision_thesis=values.pop(
            "decision_thesis",
            "Demand durability remains uncertain over three years.",
        ),
        decision_cutoff=values.pop("decision_cutoff", date(2026, 7, 24)),
        revision_cutoff=values.pop("revision_cutoff", None),
        ticker=values.pop("ticker", "NVDA"),
        market=values.pop("market", "America/New_York"),
    )
    observation = ObservationQualificationInput(
        start=values.pop("observation_start", date(2026, 7, 25)),
        end=values.pop("observation_end", date(2026, 8, 1)),
        data_available_at=values.pop(
            "data_available_at", datetime(2026, 8, 1, 20)
        ),
        method_category=values.pop("method_category", METHOD_CATEGORY),
        horizon_limit=values.pop("horizon_limit", HORIZON_LIMIT),
    )
    reflection_input = ReflectionQualificationInput(
        text=reflection,
        generated_at=values.pop("generated_at", datetime(2026, 8, 1, 20, 1)),
    )
    qualified_at = values.pop("qualified_at", datetime(2026, 8, 1, 20, 2))
    assert values == {}
    return qualify_reflection(
        source=source,
        observation=observation,
        reflection=reflection_input,
        qualified_at=qualified_at,
    )


def test_qualification_records_explicit_scope_and_short_horizon() -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Compare the original "
        "method assumptions with a bounded short-window check."
    )

    assert result.status is OutcomeFeedbackStatus.ELIGIBLE
    assert result.qualification_policy_version == QUALIFICATION_POLICY_VERSION
    assert result.reasons == ()
    assert result.candidate["source_decision_id"] == 7
    assert result.candidate["source_revision_id"] == "revision-1"
    assert result.applicability == {
        "schema_version": "1",
        "scope": "instrument",
        "instrument": "NVDA",
        "market": "America/New_York",
        "research_stages": ["analysis_methodology"],
        "research_domains": ["cross_domain"],
        "method_category": "short_term_relative_return",
        "horizon": "short_term",
    }


def test_qualification_rejects_research_claims_and_execution_advice() -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Evidence proves the old "
        "Hold rating, so buy now at the stated price target."
    )

    assert result.status is OutcomeFeedbackStatus.INELIGIBLE
    assert set(result.reasons) == {
        "contains_old_rating",
        "contains_price_target",
        "contains_current_factual_assertion",
        "contains_evidence_claim",
        "contains_execution_advice",
    }


def test_qualification_accepts_decision_cutoff_as_return_baseline() -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Use a bounded "
        "methodological check.",
        observation_start=date(2026, 7, 24),
    )

    assert result.status is OutcomeFeedbackStatus.ELIGIBLE
    assert result.reasons == ()


def test_qualification_uses_linked_revision_cutoff() -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Use a bounded "
        "methodological check.",
        revision_cutoff=date(2026, 7, 25),
        observation_start=date(2026, 7, 24),
    )

    assert result.status is OutcomeFeedbackStatus.INELIGIBLE
    assert result.reasons == ("observation_window_not_after_decision",)


def test_qualification_fails_closed_when_availability_is_not_pit() -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Use a bounded "
        "methodological check.",
        observation_start=date(2026, 7, 24),
        data_available_at=datetime(2026, 8, 2),
    )

    assert result.status is OutcomeFeedbackStatus.INELIGIBLE
    assert result.reasons == ("point_in_time_availability_invalid",)


@pytest.mark.parametrize(
    ("observation_start", "observation_end"),
    (
        (date(2026, 7, 23), date(2026, 8, 1)),
        (date(2026, 7, 24), date(2026, 7, 24)),
        (date(2026, 7, 26), date(2026, 7, 25)),
    ),
)
def test_qualification_rejects_invalid_market_local_observation_windows(
    observation_start: date,
    observation_end: date,
) -> None:
    result = _qualify(
        "Directional consistency was mixed.\nMethod lesson: Use a bounded "
        "methodological check.",
        observation_start=observation_start,
        observation_end=observation_end,
    )

    assert result.status is OutcomeFeedbackStatus.INELIGIBLE
    assert result.reasons == ("observation_window_not_after_decision",)


def test_qualification_rejects_copied_thesis_fragments() -> None:
    result = _qualify(
        "Method lesson: Recheck demand durability remains uncertain before reuse."
    )

    assert result.status is OutcomeFeedbackStatus.INELIGIBLE
    assert result.reasons == ("contains_thesis_text",)
