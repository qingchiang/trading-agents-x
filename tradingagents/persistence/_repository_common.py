"""Internal transaction helpers; no application entry points."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from tradingagents.domain.common import (
    CURRENT_RESEARCH_SCHEMA_VERSION,
    RunStatus,
)
from tradingagents.domain.decision import ResearchDecision
from tradingagents.domain.runs import (
    RunRequestSnapshot,
)
from tradingagents.persistence.models import (
    ResearchNodeRecord,
    RunRecord,
)

_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)"
)

_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}

_DECISION_SECTION_KEYS = tuple(ResearchDecision.model_fields)

class InvalidPrimaryResearchCycleError(ValueError):
    """A requested Primary Research Cycle is not an active Full Cycle."""

_SAFE_METRIC_KEYS = {
    "llm_calls",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "cache_hit_input_tokens",
    "cache_miss_input_tokens",
    "reasoning_output_tokens",
    "detailed_usage_calls",
    "wall_time_seconds",
}

def _utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None

def _sanitize_text(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    from tradingagents.credentials import credential_redactor

    redacted = _SECRET_RE.sub(r"\1\2[REDACTED]", credential_redactor()(str(value)))
    return redacted[:limit]

def _sanitize_payload(value: Any, key: str = "") -> Any:
    if key in _SAFE_METRIC_KEYS and isinstance(value, int | float):
        return value
    if not (key == "key_required" and isinstance(value, bool)) and any(
        fragment in key.casefold()
        for fragment in ("key", "secret", "token", "password", "authorization")
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, key) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, limit=10_000)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

class RunNotFoundError(LookupError):
    pass

class InvalidRunTransitionError(RuntimeError):
    pass

class IdempotencyConflictError(RuntimeError):
    pass

class ArtifactConflictError(RuntimeError):
    pass

class EvidenceConflictError(RuntimeError):
    pass

class EvidenceNotSealedError(RuntimeError):
    pass

def _validate_restoration(session: Session, restore_ids: set[str]) -> None:
    """Validate the same restoration preconditions in previews and write transactions."""
    restoring_nodes = {
        node.run_id: node
        for node in session.scalars(
            select(ResearchNodeRecord).where(ResearchNodeRecord.run_id.in_(restore_ids))
        )
    }
    for run_id, node in restoring_nodes.items():
        if node.research_kind == "full":
            full = session.get(RunRecord, run_id)
            if (
                full.status != RunStatus.SUCCEEDED.value
                or full.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION
            ):
                raise InvalidRunTransitionError(
                    "restored Full must remain a valid current Full Baseline"
                )
    restoring_slots: set[tuple[str, date]] = set()
    for run_id, node in restoring_nodes.items():
        if node.research_kind != "incremental":
            continue
        baseline = session.get(RunRecord, node.full_baseline_run_id)
        baseline_node = session.get(ResearchNodeRecord, node.full_baseline_run_id)
        if baseline is None or (baseline.trashed_at is not None and baseline.id not in restore_ids):
            raise InvalidRunTransitionError(
                "an Incremental cannot be restored while its Full remains in Trash"
            )
        run = session.get(RunRecord, run_id)
        baseline_request = RunRequestSnapshot.model_validate(baseline.request_json)
        incremental_request = RunRequestSnapshot.model_validate(run.request_json)
        if (
            baseline_node is None
            or baseline_node.research_kind != "full"
            or baseline.status != RunStatus.SUCCEEDED.value
            or baseline.research_schema_version != CURRENT_RESEARCH_SCHEMA_VERSION
            or baseline_request.ticker != incremental_request.ticker
            or baseline_request.analysis_date >= incremental_request.analysis_date
        ):
            raise InvalidRunTransitionError(
                "restored Incremental must retain a valid current Full Baseline"
            )
        slot = (node.full_baseline_run_id, run.incremental_cutoff)
        if slot in restoring_slots:
            raise InvalidRunTransitionError("restore contains duplicate same-Cycle/cutoff slots")
        restoring_slots.add(slot)
        conflict = session.scalar(
            select(RunRecord.id).where(
                RunRecord.research_kind == "incremental",
                RunRecord.full_baseline_run_id == node.full_baseline_run_id,
                RunRecord.incremental_cutoff == run.incremental_cutoff,
                RunRecord.trashed_at.is_(None),
                RunRecord.status.in_(
                    (
                        RunStatus.QUEUED.value,
                        RunStatus.RUNNING.value,
                        RunStatus.SUCCEEDED.value,
                    )
                ),
                RunRecord.id.not_in(restore_ids),
            )
        )
        if conflict is not None:
            raise InvalidRunTransitionError(
                "restore conflicts with an active slot for the same Cycle/cutoff"
            )

def _lifecycle_scope(
    connection: Connection, action: str, run_ids: tuple[str, ...]
) -> tuple[str, ...]:
    runs = RunRecord.__table__
    nodes = ResearchNodeRecord.__table__
    records = {
        row["id"]: row
        for row in connection.execute(
            select(runs.c.id, runs.c.trashed_at).where(runs.c.id.in_(run_ids))
        ).mappings()
    }
    full_ids = tuple(
        connection.scalars(
            select(nodes.c.run_id).where(
                nodes.c.run_id.in_(run_ids), nodes.c.research_kind == "full"
            )
        )
    )
    affected = set(records)
    if action == "restore":
        roots = tuple(key for key in full_ids if records[key]["trashed_at"] is not None)
        affected.update(
            connection.scalars(select(runs.c.id).where(runs.c.trash_cascade_full_run_id.in_(roots)))
        )
    else:
        query = (
            select(nodes.c.run_id)
            .join(runs, runs.c.id == nodes.c.run_id)
            .where(nodes.c.full_baseline_run_id.in_(full_ids))
        )
        if action == "trash":
            query = query.where(runs.c.trashed_at.is_(None))
        affected.update(connection.scalars(query))
    return tuple(sorted(affected))

def _check_lifecycle_scope(
    connection: Connection, action: str, run_ids: tuple[str, ...], expected: tuple[str, ...] | None
) -> None:
    actual = _lifecycle_scope(connection, action, run_ids)
    if expected is not None and set(actual) != set(expected):
        raise InvalidRunTransitionError(
            "The affected research changed. Refresh the preview and confirm again."
        )
