"""Deterministically rebuild successful structured recoveries from run events."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import (
    ArtifactGenerationMethod,
    RunEvent,
    StructuredRecoveryNotice,
)

_RETRY_EVENTS = {
    "node.output_retry": "structured",
    "node.numeric_audit_retry": "numeric",
}
_RECOVERED_EVENTS = {
    "node.output_recovered": "structured",
    "node.numeric_audit_recovered": "numeric",
}
_FAILED_EVENTS = {
    "node.output_failed": "structured",
    "node.numeric_audit_degraded": "numeric",
}


def rebuild_structured_recoveries(
    events: Iterable[RunEvent],
) -> tuple[StructuredRecoveryNotice, ...]:
    """Pair persisted retry/recovered events within one attempt and node."""

    pending: dict[tuple[int, str, str], list[RunEvent]] = {}
    notices: list[StructuredRecoveryNotice] = []
    for event in sorted(events, key=lambda item: item.sequence):
        if event.node is None:
            continue
        family = _RETRY_EVENTS.get(event.event_type)
        if family is not None:
            pending.setdefault((event.attempt, event.node, family), []).append(event)
            continue
        family = _RECOVERED_EVENTS.get(event.event_type)
        if family is not None:
            key = (event.attempt, event.node, family)
            retries = pending.pop(key, [])
            if not retries:
                continue
            method = event.payload.get("method") or retries[-1].payload.get("method")
            reason = retries[0].payload.get("reason_code")
            if not isinstance(method, str) or not isinstance(reason, str):
                continue
            issues = tuple(
                dict.fromkeys(
                    str(issue)
                    for retry in retries
                    for issue in retry.payload.get("validation_issues", ())
                    if isinstance(issue, str)
                )
            )
            try:
                notices.append(
                    StructuredRecoveryNotice(
                        attempt=event.attempt,
                        node=event.node,
                        initial_reason_code=reason,
                        recovery_method=ArtifactGenerationMethod(method),
                        validation_issue_codes=issues,
                        retry_count=len(retries),
                        recovered_at=event.created_at,
                    )
                )
            except ValueError:
                continue
            continue
        family = _FAILED_EVENTS.get(event.event_type)
        if family is not None:
            pending.pop((event.attempt, event.node, family), None)
    return tuple(notices)
