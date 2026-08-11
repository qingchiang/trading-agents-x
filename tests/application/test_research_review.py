from __future__ import annotations

import pytest

from tradingagents.application.research_review import derive_review_status


@pytest.mark.parametrize(
    ("outcome_status", "outcome_error", "reflection_status", "next_retry", "feedback_status", "expected"),
    (
        ("pending", None, None, None, None, "awaiting_observation"),
        ("pending", "vendor unavailable", None, None, None, "observation_delayed"),
        ("resolved", None, "pending", None, None, "awaiting_reflection"),
        ("resolved", None, "retryable_failure", "2026-08-01T20:00:00Z", None, "reflection_retry_scheduled"),
        ("resolved", None, "retryable_failure", None, None, "reflection_failed"),
        ("resolved", None, "invalid", None, None, "reflection_invalid"),
        ("resolved", None, "generated", None, "eligible", "feedback_available"),
        ("resolved", None, "generated", None, "ineligible", "feedback_ineligible"),
        ("resolved", None, "generated", None, "retired", "feedback_retired"),
    ),
)
def test_derive_review_status_uses_authoritative_lifecycle_precedence(
    outcome_status: str,
    outcome_error: str | None,
    reflection_status: str | None,
    next_retry: str | None,
    feedback_status: str | None,
    expected: str,
) -> None:
    assert derive_review_status(
        outcome_status=outcome_status,
        outcome_error=outcome_error,
        reflection_status=reflection_status,
        reflection_next_retry_at=next_retry,
        feedback_status=feedback_status,
    ) == expected


@pytest.mark.parametrize(
    ("outcome_status", "reflection_status", "feedback_status"),
    (
        ("pending", "pending", None),
        ("resolved", None, None),
        ("resolved", "generated", None),
        ("resolved", "invalid", "eligible"),
    ),
)
def test_derive_review_status_fails_closed_for_inconsistent_lifecycle_data(
    outcome_status: str,
    reflection_status: str | None,
    feedback_status: str | None,
) -> None:
    assert derive_review_status(
        outcome_status=outcome_status,
        outcome_error=None,
        reflection_status=reflection_status,
        reflection_next_retry_at=None,
        feedback_status=feedback_status,
    ) == "lifecycle_inconsistent"
