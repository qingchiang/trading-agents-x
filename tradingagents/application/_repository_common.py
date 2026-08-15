"""Transactional repository for runs, events, reports, and research memory."""

# This module intentionally re-exports the shared repository vocabulary to the
# internal stores, while the public compatibility surface remains repository.py.
# ruff: noqa: F401

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.dataflows.symbol_utils import market_timezone
from tradingagents.version import __version__

from .contracts import (
    AnalysisRequest,
    AnalysisResult,
    AnalystReport,
    ArtifactGenerationMethod,
    ArtifactGenerationObservation,
    DebateAgenda,
    DecisionBrief,
    DecisionNumericAuditAppendix,
    EvidenceBundle,
    EvidenceSealView,
    JudgeDraft,
    MemoryContext,
    MemoryOutcome,
    MemoryRecord,
    NumericAuditStatus,
    RebuttalReview,
    RecentInstrument,
    ResearchArtifact,
    ResearchArtifactDraft,
    ResearchCase,
    ResearchDecision,
    ResearchRating,
    ResearchUpdateAudit,
    ResearchWarning,
    RiskReview,
    RunAttemptView,
    RunEvent,
    RunMetrics,
    RunPage,
    RunStatus,
    RunSummaryView,
    RunTrashState,
    RunView,
    StructuredRecoveryNotice,
)
from .database import (
    Base,
    DecisionRecord,
    OutcomeFeedbackRecord,
    OutcomeRecord,
    ReflectionAttemptRecord,
    ReflectionGenerationCycleRecord,
    ReflectionRecord,
    ResearchChainRecord,
    ResearchRevisionRecord,
    RunArtifactRecord,
    RunAttemptRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
    create_sqlite_engine,
)
from .metrics import merge_run_metrics
from .outcome_feedback import (
    ADJUSTMENT_SEMANTICS,
    HORIZON_LIMIT,
    METHOD_CATEGORY,
    METHOD_VERSION,
    OBSERVATION_LIMITATIONS,
    PRICE_SEMANTICS,
    FeedbackSource,
    ObservationQualificationInput,
    OutcomeFeedbackRetirementReason,
    OutcomeFeedbackStatus,
    OutcomeObservationStatus,
    OutcomeReflectionStatus,
    ReflectionQualificationInput,
    qualify_reflection,
    reflection_candidate_lesson,
)
from .outcome_schedule import earliest_outcome_check_at
from .recoveries import rebuild_structured_recoveries
from .reflection import OUTCOME_REFLECTION_SCHEMA_VERSION, OutcomeReflectionDraft
from .reporting import order_reports
from .research import (
    CoverageAttestation,
    CurrentResearchState,
    EffectiveEvidenceSnapshot,
    IndeterminateReason,
    ResearchChain,
    ResearchChangeConclusion,
    ResearchExecutionStrategy,
    ResearchRevision,
    ResearchRevisionDraft,
    ResearchRevisionRole,
    RevisionDelta,
    UpdateSummary,
)
from .research_review import derive_review_status, review_status_in_group
from .sanitization import sanitize_text
from .settings import AppSettings

_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}
_REFLECTION_RETRY_DELAYS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))


def _numeric_audit_warning_message(
    appendix: DecisionNumericAuditAppendix | None,
) -> str:
    labels = tuple(
        item.reference_label or item.component_path
        for item in (appendix.omitted_components if appendix else ())
    )
    omitted = f": {', '.join(labels)}" if labels else ""
    return (
        "Optional numeric components were omitted because their audit failed"
        f"{omitted}. The qualitative decision remains audited."
    )


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
    return sanitize_text(str(value), limit=limit)


def _usage_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _invalid_candidate_audit(value: str | None) -> tuple[str | None, str | None, int | None]:
    """Retain a bounded, redacted diagnostic copy without treating it as content."""
    if not isinstance(value, str):
        return None, None, None
    return (
        _sanitize_text(value, limit=4_000),
        sha256(value.encode("utf-8")).hexdigest(),
        len(value),
    )


def _sanitize_payload(value: Any, key: str = "") -> Any:
    if key in _SAFE_METRIC_KEYS and isinstance(value, int | float):
        return value
    if any(
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


class ResearchChainNotFoundError(LookupError):
    pass


class ResearchRevisionNotFoundError(LookupError):
    pass


class InvalidResearchBaselineError(ValueError):
    """Raised when an update does not target the current Research Chain head."""


class OutcomeReflectionRegenerationNotFoundError(LookupError):
    pass


class OutcomeReflectionRegenerationConflictError(RuntimeError):
    def __init__(self, message: str, *, active_cycle_id: str | None = None):
        super().__init__(message)
        self.active_cycle_id = active_cycle_id


class OutcomeFeedbackRetirementNotFoundError(LookupError):
    pass


class OutcomeFeedbackRetirementConflictError(RuntimeError):
    pass
