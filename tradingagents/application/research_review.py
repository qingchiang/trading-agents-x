"""Read-model status derivation for the Research Review collection."""

from __future__ import annotations

from typing import Literal

ReviewStatus = Literal[
    "awaiting_observation",
    "observation_delayed",
    "awaiting_reflection",
    "reflection_retry_scheduled",
    "reflection_failed",
    "reflection_invalid",
    "feedback_available",
    "feedback_ineligible",
    "feedback_retired",
    "lifecycle_inconsistent",
]

ReviewStatusGroup = Literal[
    "needs_attention",
    "in_progress",
    "feedback_available",
    "feedback_ineligible_or_retired",
    "all",
]


def derive_review_status(
    *,
    outcome_status: str,
    outcome_error: str | None,
    reflection_status: str | None,
    reflection_next_retry_at: object | None,
    feedback_status: str | None,
) -> ReviewStatus:
    """Derive one fail-closed display status from persisted lifecycle rows."""
    if outcome_status == "pending":
        if reflection_status is not None or feedback_status is not None:
            return "lifecycle_inconsistent"
        return "observation_delayed" if outcome_error else "awaiting_observation"
    if outcome_status != "resolved" or outcome_error:
        return "lifecycle_inconsistent"
    if reflection_status == "pending":
        return "awaiting_reflection" if feedback_status is None else "lifecycle_inconsistent"
    if reflection_status == "retryable_failure":
        if feedback_status is not None:
            return "lifecycle_inconsistent"
        return (
            "reflection_retry_scheduled"
            if reflection_next_retry_at is not None
            else "reflection_failed"
        )
    if reflection_status == "invalid":
        return "reflection_invalid" if feedback_status is None else "lifecycle_inconsistent"
    if reflection_status != "generated" or feedback_status is None:
        return "lifecycle_inconsistent"
    return {
        "eligible": "feedback_available",
        "ineligible": "feedback_ineligible",
        "retired": "feedback_retired",
    }.get(feedback_status, "lifecycle_inconsistent")


def review_status_in_group(status: ReviewStatus, group: str | None) -> bool:
    """Apply product filter groups without persisting another lifecycle state."""
    if group in {None, "", "all"}:
        return True
    return status in {
        "needs_attention": {"reflection_failed", "reflection_invalid"},
        "in_progress": {
            "awaiting_observation",
            "observation_delayed",
            "awaiting_reflection",
            "reflection_retry_scheduled",
        },
        "feedback_available": {"feedback_available"},
        "feedback_ineligible_or_retired": {
            "feedback_ineligible",
            "feedback_retired",
        },
    }.get(group, set())
