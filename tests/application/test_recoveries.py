"""Structured recovery notices rebuilt from durable run events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradingagents.application.contracts import (
    ArtifactGenerationMethod,
    RunEvent,
)
from tradingagents.application.recoveries import rebuild_structured_recoveries


def _event(
    sequence: int,
    event_type: str,
    *,
    attempt: int = 1,
    node: str = "analyst.fundamentals.audit",
    payload: dict | None = None,
) -> RunEvent:
    return RunEvent(
        run_id="run-1",
        sequence=sequence,
        attempt=attempt,
        event_type=event_type,
        node=node,
        payload=payload or {},
        created_at=datetime(2026, 8, 1, tzinfo=UTC)
        + timedelta(seconds=sequence),
    )


def test_rebuilds_structured_and_numeric_recoveries_by_attempt_and_node() -> None:
    events = (
        _event(
            1,
            "node.output_retry",
            payload={
                "method": "tool_call_recovered",
                "reason_code": "semantic_validation",
                "validation_issues": ["semantic.refs.missing"],
            },
        ),
        _event(
            2,
            "node.output_recovered",
            payload={
                "method": "tool_call_recovered",
                "reason_code": "semantic_validation",
            },
        ),
        _event(
            3,
            "node.numeric_audit_retry",
            node="committee.final.serialize.numeric",
            payload={
                "method": "tool_call_recovered",
                "reason_code": "schema_validation",
                "validation_issues": ["schema.reference_range"],
            },
        ),
        _event(
            4,
            "node.numeric_audit_recovered",
            node="committee.final.serialize.numeric",
            payload={"method": "tool_call_recovered"},
        ),
    )

    notices = rebuild_structured_recoveries(reversed(events))

    assert [notice.node for notice in notices] == [
        "analyst.fundamentals.audit",
        "committee.final.serialize.numeric",
    ]
    assert notices[0].initial_reason_code == "semantic_validation"
    assert notices[0].validation_issue_codes == ("semantic.refs.missing",)
    assert notices[0].recovery_method is ArtifactGenerationMethod.TOOL_CALL_RECOVERED
    assert notices[0].retry_count == 1


def test_failed_or_unpaired_recovery_events_do_not_create_notices() -> None:
    events = (
        _event(
            1,
            "node.output_retry",
            payload={
                "method": "json_mode_recovered",
                "reason_code": "schema_validation",
            },
        ),
        _event(2, "node.output_failed"),
        _event(
            3,
            "node.output_recovered",
            payload={"method": "raw_json_recovered"},
        ),
        _event(
            4,
            "node.output_retry",
            attempt=2,
            payload={
                "method": "tool_call_recovered",
                "reason_code": "non_json_response",
            },
        ),
    )

    assert rebuild_structured_recoveries(events) == ()
