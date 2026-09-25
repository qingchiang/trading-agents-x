"""Submission replay requires the originally recorded identity."""

from typing import Any

from tradingagents.persistence._repository_common import IdempotencyConflictError
from tradingagents.persistence.models import RunRecord


def matches_submission(record: RunRecord, identity: dict[str, Any]) -> bool:
    if record.submission_json is None:
        raise IdempotencyConflictError(
            "Original submission identity was not recorded; use a new idempotency key"
        )
    return record.submission_json == identity
