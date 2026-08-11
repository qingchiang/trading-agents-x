"""Transactional repository for runs, events, reports, and research memory."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
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
    evaluate_next_update_policy,
)
from .research_review import derive_review_status, review_status_in_group
from .settings import AppSettings

_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)"
)
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
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None else None


def _sanitize_text(value: str | None, limit: int = 2000) -> str | None:
    if value is None:
        return None
    redacted = _SECRET_RE.sub(r"\1\2[REDACTED]", str(value))
    return redacted[:limit]


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
    """Raised when an update does not target the current Eligible Baseline."""


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


class RunRepository:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        settings.prepare_filesystem()
        self.engine = create_sqlite_engine(
            settings.database_path,
            busy_timeout_ms=settings.busy_timeout_ms,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, class_=Session)

    def create_schema(self) -> None:
        """Create the current schema for tests; production entry points run Alembic."""
        Base.metadata.create_all(self.engine)

    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_chain_requested: bool = False,
    ) -> tuple[RunView, bool]:
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        try:
            with self.sessions.begin() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = session.scalar(
                        select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                    )
                    if existing is not None:
                        if (
                            existing.request_json != request_json
                            or existing.source_run_id != source_run_id
                            or existing.research_chain_requested != research_chain_requested
                        ):
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a different request"
                            )
                        return self._view(existing), False
                if source_run_id is not None:
                    source = session.get(RunRecord, source_run_id)
                    if source is None:
                        raise RunNotFoundError(source_run_id)
                    if source.status not in _TERMINAL_STATUSES:
                        raise InvalidRunTransitionError(
                            "source run must be terminal before it can be "
                            "used as a research template"
                        )
                run_id = str(uuid4())
                record = RunRecord(
                    id=run_id,
                    source_run_id=source_run_id,
                    idempotency_key=idempotency_key,
                    research_chain_requested=research_chain_requested,
                    status=RunStatus.QUEUED.value,
                    request_json=request_json,
                    config_json=_sanitize_payload(config_snapshot),
                    version=__version__,
                    current_attempt=1,
                    cancel_requested=False,
                    metrics_json=RunMetrics().model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.add(
                    RunAttemptRecord(
                        run_id=run_id,
                        attempt=1,
                        status=RunStatus.QUEUED.value,
                        checkpoint_thread_id=self.checkpoint_thread_id(run_id, 1),
                        metrics_json=RunMetrics().model_dump(mode="json"),
                    )
                )
        except IntegrityError as exc:
            if idempotency_key is None:
                raise
            with self.sessions() as session:
                existing = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                if (
                    existing.request_json != request_json
                    or existing.source_run_id != source_run_id
                    or existing.research_chain_requested != research_chain_requested
                ):
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view(existing), False
        return self.get_run(run_id), True

    def create_chain_update(
        self,
        chain_id: str,
        baseline_revision_id: str,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        execution_strategy: ResearchExecutionStrategy,
        idempotency_key: str | None = None,
    ) -> tuple[RunView, bool]:
        """Atomically create or resolve one Full update for the current head."""
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            chain = session.get(ResearchChainRecord, chain_id)
            if chain is None:
                raise ResearchChainNotFoundError(chain_id)
            baseline = session.get(ResearchRevisionRecord, baseline_revision_id)
            if baseline is None or baseline.chain_id != chain.id:
                raise InvalidResearchBaselineError(
                    "update baseline must belong to the same Research Chain"
                )
            if request.ticker != chain.instrument:
                raise InvalidResearchBaselineError(
                    "update Instrument must match the Research Chain"
                )
            if request.analysis_date <= baseline.cutoff:
                raise InvalidResearchBaselineError(
                    "update cutoff must be strictly later than the Eligible Baseline"
                )
            existing_exact = session.scalar(
                select(RunRecord)
                .where(
                    RunRecord.research_chain_id == chain.id,
                    RunRecord.baseline_revision_id == baseline.id,
                    RunRecord.request_json == request_json,
                )
                .order_by(RunRecord.created_at)
            )
            if existing_exact is not None:
                return self._view(existing_exact), False
            if chain.current_revision_id != baseline.id:
                raise InvalidResearchBaselineError(
                    "update must target the current Research Chain head"
                )
            active = session.scalar(
                select(RunRecord).where(
                    RunRecord.research_chain_id == chain.id,
                    RunRecord.status.in_((RunStatus.QUEUED.value, RunStatus.RUNNING.value)),
                )
            )
            if active is not None:
                raise InvalidResearchBaselineError(
                    "a queued or running update already exists for this Research Chain"
                )
            if idempotency_key:
                reused = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if reused is not None:
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    )
            run_id = str(uuid4())
            record = RunRecord(
                id=run_id,
                idempotency_key=idempotency_key,
                status=RunStatus.QUEUED.value,
                research_chain_requested=False,
                update_intent_id=str(uuid4()),
                research_chain_id=chain.id,
                baseline_revision_id=baseline.id,
                research_execution_strategy=execution_strategy.value,
                request_json=request_json,
                config_json=_sanitize_payload(config_snapshot),
                version=__version__,
                current_attempt=1,
                cancel_requested=False,
                metrics_json=RunMetrics().model_dump(mode="json"),
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.add(
                RunAttemptRecord(
                    run_id=run_id,
                    attempt=1,
                    status=RunStatus.QUEUED.value,
                    checkpoint_thread_id=self.checkpoint_thread_id(run_id, 1),
                    metrics_json=RunMetrics().model_dump(mode="json"),
                )
            )
        return self.get_run(run_id), True

    def set_research_update_audit(
        self,
        run_id: str,
        audit: ResearchUpdateAudit,
    ) -> None:
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            record.research_update_audit_json = audit.model_dump(mode="json")
            record.updated_at = _utc_naive()

    @staticmethod
    def checkpoint_thread_id(run_id: str, attempt: int) -> str:
        return f"run:{run_id}:attempt:{attempt}"

    def get_run(self, run_id: str) -> RunView:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return self._view(record)

    def list_runs(
        self,
        *,
        trash_state: RunTrashState = RunTrashState.ACTIVE,
        status: RunStatus | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> RunPage:
        limit = min(max(1, limit), 200)
        offset = max(0, offset)
        filters = []
        if trash_state is RunTrashState.ACTIVE:
            filters.append(RunRecord.trashed_at.is_(None))
        elif trash_state is RunTrashState.TRASHED:
            filters.append(RunRecord.trashed_at.is_not(None))
        if status is not None:
            filters.append(RunRecord.status == status.value)
        if q and (query := q.strip().casefold()):
            filters.append(
                or_(
                    func.lower(RunRecord.id).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(
                        func.coalesce(
                            func.json_extract(
                                RunRecord.request_json,
                                "$.ticker",
                            ),
                            "",
                        )
                    ).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_local_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                )
            )
        stmt = (
            select(RunRecord, DecisionRecord.rating)
            .outerjoin(DecisionRecord, DecisionRecord.run_id == RunRecord.id)
            .where(*filters)
            .order_by(RunRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_stmt = select(func.count()).select_from(RunRecord).where(*filters)
        with self.sessions() as session:
            return RunPage(
                items=tuple(
                    self._summary(record, rating) for record, rating in session.execute(stmt)
                ),
                total=int(session.scalar(count_stmt) or 0),
                limit=limit,
                offset=offset,
            )

    def trash_runs(
        self,
        run_ids: tuple[str, ...],
    ) -> tuple[tuple[RunView, ...], int]:
        """Atomically trash terminal runs after validating the full batch."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            records = {
                record.id: record
                for record in session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            invalid = [
                record.id
                for record in records.values()
                if record.trashed_at is None and record.status not in _TERMINAL_STATUSES
            ]
            if invalid:
                raise InvalidRunTransitionError(
                    "only terminal runs can be trashed: " + ", ".join(invalid)
                )
            changed = 0
            for run_id in run_ids:
                record = records[run_id]
                if record.trashed_at is None:
                    record.trashed_at = now
                    record.updated_at = now
                    changed += 1
            session.flush()
            views = tuple(self._view(records[run_id]) for run_id in run_ids)
        return views, changed

    def restore_runs(
        self,
        run_ids: tuple[str, ...],
    ) -> tuple[tuple[RunView, ...], int]:
        """Atomically restore trashed runs; repeated requests are idempotent."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            records = {
                record.id: record
                for record in session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            changed = 0
            for run_id in run_ids:
                record = records[run_id]
                if record.trashed_at is not None:
                    record.trashed_at = None
                    record.updated_at = now
                    changed += 1
            session.flush()
            views = tuple(self._view(records[run_id]) for run_id in run_ids)
        return views, changed

    def set_instrument_name(
        self,
        run_id: str,
        instrument_name: str | None,
    ) -> RunView:
        """Persist a best-effort display name without affecting run status."""
        normalized = (
            instrument_name.strip()[:300]
            if isinstance(instrument_name, str) and instrument_name.strip()
            else None
        )
        if normalized is None:
            return self.get_run(run_id)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.instrument_name = normalized
            record.updated_at = _utc_naive()
        return self.get_run(run_id)

    def set_instrument_local_name(
        self,
        run_id: str,
        instrument_local_name: str | None,
    ) -> RunView:
        """Persist one cutoff-safe market-local display name when available."""
        normalized = (
            instrument_local_name.strip()[:300]
            if isinstance(instrument_local_name, str) and instrument_local_name.strip()
            else None
        )
        if normalized is None:
            return self.get_run(run_id)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.instrument_local_name is None:
                record.instrument_local_name = normalized
                record.updated_at = _utc_naive()
        return self.get_run(run_id)

    def recent_instruments(self, *, limit: int = 20) -> tuple[RecentInstrument, ...]:
        """Return the latest non-trashed use of each canonical ticker."""
        limit = min(max(1, limit), 100)
        ticker = func.json_extract(
            RunRecord.request_json,
            "$.ticker",
        )
        ranked = (
            select(
                ticker.label("ticker"),
                RunRecord.instrument_name.label("instrument_name"),
                RunRecord.instrument_local_name.label("instrument_local_name"),
                RunRecord.created_at.label("last_used_at"),
                func.row_number()
                .over(
                    partition_by=func.lower(ticker),
                    order_by=(
                        RunRecord.created_at.desc(),
                        RunRecord.id.desc(),
                    ),
                )
                .label("ticker_rank"),
            )
            .where(
                RunRecord.trashed_at.is_(None),
                ticker.is_not(None),
            )
            .subquery()
        )
        stmt = (
            select(
                ranked.c.ticker,
                ranked.c.instrument_name,
                ranked.c.instrument_local_name,
                ranked.c.last_used_at,
            )
            .where(ranked.c.ticker_rank == 1)
            .order_by(ranked.c.last_used_at.desc(), ranked.c.ticker)
            .limit(limit)
        )
        with self.engine.connect() as connection:
            return tuple(
                RecentInstrument(
                    ticker=str(row.ticker),
                    instrument_name=row.instrument_name,
                    instrument_local_name=row.instrument_local_name,
                    last_used_at=_aware(row.last_used_at),
                )
                for row in connection.execute(stmt)
            )

    def purge_expired_trash(
        self,
        *,
        cutoff: datetime,
        batch_size: int = 50,
    ) -> int:
        """Permanently remove one bounded batch of expired trashed runs.

        Checkpoint rows and application-owned rows are deleted in the same
        SQLite write transaction. A checkpoint failure therefore preserves the
        run and all of its application data for a later retry.
        """
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        batch_size = min(max(1, batch_size), 200)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                run_ids = tuple(
                    connection.scalars(
                        select(RunRecord.id)
                        .where(
                            RunRecord.trashed_at.is_not(None),
                            RunRecord.trashed_at <= cutoff,
                        )
                        .order_by(RunRecord.trashed_at, RunRecord.id)
                        .limit(batch_size)
                    )
                )
                if not run_ids:
                    connection.commit()
                    return 0
                checkpoint_threads = tuple(
                    dict.fromkeys(
                        connection.scalars(
                            select(RunAttemptRecord.checkpoint_thread_id)
                            .where(RunAttemptRecord.run_id.in_(run_ids))
                            .order_by(RunAttemptRecord.id)
                        )
                    )
                )
                for checkpoint_thread in checkpoint_threads:
                    connection.exec_driver_sql(
                        "DELETE FROM writes WHERE thread_id = ?",
                        (checkpoint_thread,),
                    )
                    connection.exec_driver_sql(
                        "DELETE FROM checkpoints WHERE thread_id = ?",
                        (checkpoint_thread,),
                    )
                deleted = connection.execute(
                    delete(RunRecord).where(
                        RunRecord.id.in_(run_ids),
                        RunRecord.trashed_at.is_not(None),
                        RunRecord.trashed_at <= cutoff,
                    )
                ).rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return int(deleted or 0)

    def claim_next(self, worker_id: str, lease_seconds: int) -> RunView | None:
        """Atomically claim queued work or recover an expired running lease."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            candidate = (
                connection.execute(
                    select(
                        RunRecord.id,
                        RunRecord.status,
                        RunRecord.current_attempt,
                        RunAttemptRecord.started_at.label("attempt_started_at"),
                    )
                    .join(
                        RunAttemptRecord,
                        and_(
                            RunAttemptRecord.run_id == RunRecord.id,
                            RunAttemptRecord.attempt == RunRecord.current_attempt,
                        ),
                    )
                    .where(
                        RunRecord.trashed_at.is_(None),
                        or_(
                            RunRecord.status == RunStatus.QUEUED.value,
                            and_(
                                RunRecord.status == RunStatus.RUNNING.value,
                                RunRecord.lease_expires_at < now,
                                RunRecord.cancel_requested.is_(False),
                            ),
                        ),
                    )
                    .order_by(RunRecord.created_at)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if candidate is None:
                connection.commit()
                return None
            resuming = candidate["attempt_started_at"] is not None
            connection.execute(
                update(RunRecord)
                .where(RunRecord.id == candidate["id"])
                .values(
                    status=RunStatus.RUNNING.value,
                    lease_owner=worker_id,
                    lease_expires_at=expires,
                    started_at=func.coalesce(RunRecord.started_at, now),
                    updated_at=now,
                )
            )
            attempt_values: dict[str, Any] = {
                "status": RunStatus.RUNNING.value,
                "lease_owner": worker_id,
                "lease_expires_at": expires,
                "started_at": func.coalesce(RunAttemptRecord.started_at, now),
            }
            if resuming:
                attempt_values["resume_count"] = RunAttemptRecord.resume_count + 1
            connection.execute(
                update(RunAttemptRecord)
                .where(
                    RunAttemptRecord.run_id == candidate["id"],
                    RunAttemptRecord.attempt == candidate["current_attempt"],
                )
                .values(**attempt_values)
            )
            connection.commit()
        return self.get_run(candidate["id"])

    def claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> RunView:
        """Claim a specific queued run for the synchronous Python API."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status != RunStatus.QUEUED.value:
                raise InvalidRunTransitionError(f"run {run_id} is {record.status}, expected queued")
            record.status = RunStatus.RUNNING.value
            record.lease_owner = worker_id
            record.lease_expires_at = expires
            record.started_at = record.started_at or now
            record.updated_at = now
            attempt = session.scalar(
                select(RunAttemptRecord).where(
                    RunAttemptRecord.run_id == run_id,
                    RunAttemptRecord.attempt == record.current_attempt,
                )
            )
            attempt.status = RunStatus.RUNNING.value
            attempt.lease_owner = worker_id
            attempt.lease_expires_at = expires
            if attempt.started_at is not None:
                attempt.resume_count += 1
            attempt.started_at = attempt.started_at or now
        return self.get_run(run_id)

    def heartbeat(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            changed = session.execute(
                update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status == RunStatus.RUNNING.value,
                    RunRecord.lease_owner == worker_id,
                )
                .values(lease_expires_at=expires, updated_at=now)
            ).rowcount
            if changed:
                record = session.get(RunRecord, run_id)
                session.execute(
                    update(RunAttemptRecord)
                    .where(
                        RunAttemptRecord.run_id == run_id,
                        RunAttemptRecord.attempt == record.current_attempt,
                    )
                    .values(lease_expires_at=expires)
                )
        return bool(changed)

    def release_claim(
        self,
        run_id: str,
        worker_id: str,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunView:
        """Return an interrupted run to the queue without changing its attempt."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value or record.lease_owner != worker_id:
                raise InvalidRunTransitionError(f"run {run_id} is not claimed by {worker_id}")
            record.status = RunStatus.QUEUED.value
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = now
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.QUEUED.value
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            self._merge_metrics(record, attempt, metrics)
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status == RunStatus.QUEUED.value:
                record.status = RunStatus.CANCELLED.value
                record.cancel_requested = True
                record.finished_at = now
                attempt = self._attempt(session, record)
                attempt.status = RunStatus.CANCELLED.value
                attempt.finished_at = now
            elif record.status == RunStatus.RUNNING.value:
                record.cancel_requested = True
            elif record.status not in {
                RunStatus.SUCCEEDED.value,
                RunStatus.FAILED.value,
                RunStatus.CANCELLED.value,
            }:
                raise InvalidRunTransitionError(record.status)
            record.updated_at = now
        return self.get_run(run_id)

    def cancel_requested(self, run_id: str) -> bool:
        with self.sessions() as session:
            value = session.scalar(select(RunRecord.cancel_requested).where(RunRecord.id == run_id))
            if value is None:
                raise RunNotFoundError(run_id)
            return bool(value)

    def retry(self, run_id: str) -> RunView:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(f"run {run_id} is trashed")
            if record.status != RunStatus.FAILED.value:
                raise InvalidRunTransitionError(
                    f"only failed runs can be retried, got {record.status}"
                )
            checkpoint_thread_id = self._attempt(
                session,
                record,
            ).checkpoint_thread_id
            record.current_attempt += 1
            record.status = RunStatus.QUEUED.value
            record.cancel_requested = False
            record.lease_owner = None
            record.lease_expires_at = None
            record.error_code = None
            record.error_message = None
            record.finished_at = None
            record.updated_at = now
            session.add(
                RunAttemptRecord(
                    run_id=run_id,
                    attempt=record.current_attempt,
                    status=RunStatus.QUEUED.value,
                    checkpoint_thread_id=checkpoint_thread_id,
                    metrics_json=RunMetrics().model_dump(mode="json"),
                )
            )
        return self.get_run(run_id)

    def checkpoint_thread(self, run_id: str) -> str:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            attempt = self._attempt(session, record)
            return attempt.checkpoint_thread_id

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        node: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        now = _utc_naive()
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            row = connection.execute(
                select(RunRecord.current_attempt).where(RunRecord.id == run_id)
            ).first()
            if row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=row[0],
                    event_type=event_type,
                    node=node,
                    payload_json=_sanitize_payload(payload or {}),
                    created_at=now,
                )
            )
            connection.commit()
        return RunEvent(
            run_id=run_id,
            sequence=sequence,
            attempt=row[0],
            event_type=event_type,
            node=node,
            payload=_sanitize_payload(payload or {}),
            created_at=_aware(now),
        )

    def list_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[RunEvent]:
        stmt = (
            select(RunEventRecord)
            .where(
                RunEventRecord.run_id == run_id,
                RunEventRecord.sequence > max(0, after_sequence),
            )
            .order_by(RunEventRecord.sequence)
            .limit(min(max(1, limit), 2000))
        )
        with self.sessions() as session:
            records = list(session.scalars(stmt))
        return [
            RunEvent(
                run_id=record.run_id,
                sequence=record.sequence,
                attempt=record.attempt,
                event_type=record.event_type,
                node=record.node,
                payload=record.payload_json,
                created_at=_aware(record.created_at),
            )
            for record in records
        ]

    def list_recoveries(self, run_id: str) -> tuple[StructuredRecoveryNotice, ...]:
        """Return all successful structured recoveries without event-page limits."""

        event_types = tuple(
            {
                "node.output_retry",
                "node.output_recovered",
                "node.output_failed",
                "node.numeric_audit_retry",
                "node.numeric_audit_recovered",
                "node.numeric_audit_degraded",
            }
        )
        with self.sessions() as session:
            records = tuple(
                session.scalars(
                    select(RunEventRecord)
                    .where(
                        RunEventRecord.run_id == run_id,
                        RunEventRecord.event_type.in_(event_types),
                    )
                    .order_by(RunEventRecord.sequence)
                )
            )
        events = tuple(
            RunEvent(
                run_id=record.run_id,
                sequence=record.sequence,
                attempt=record.attempt,
                event_type=record.event_type,
                node=record.node,
                payload=record.payload_json,
                created_at=_aware(record.created_at),
            )
            for record in records
        )
        return rebuild_structured_recoveries(events)

    def append_artifact(
        self,
        run_id: str,
        draft: ResearchArtifactDraft,
    ) -> tuple[ResearchArtifact, RunEvent | None]:
        """Persist one artifact and its metadata event in one transaction.

        A replay that emits the same stage output returns the existing artifact
        without creating another event, including when the replay is a later
        retry attempt.
        """
        now = _utc_naive()
        table = RunArtifactRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            run_row = connection.execute(
                select(
                    RunRecord.current_attempt,
                    RunRecord.status,
                ).where(RunRecord.id == run_id)
            ).first()
            if run_row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            if run_row.status != RunStatus.RUNNING.value:
                connection.rollback()
                raise InvalidRunTransitionError(run_row.status)
            existing = (
                connection.execute(
                    select(table).where(
                        table.c.run_id == run_id,
                        table.c.stage == draft.stage,
                        table.c.role == draft.role,
                        table.c.round == draft.round,
                        table.c.prompt_version == draft.prompt_version,
                    )
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["content_hash"] != draft.content_hash:
                    connection.rollback()
                    raise ArtifactConflictError("artifact identity replayed with different content")
                connection.commit()
                return self._artifact(existing), None

            artifact_id = str(uuid4())
            attempt = run_row.current_attempt
            connection.execute(
                table.insert().values(
                    id=artifact_id,
                    run_id=run_id,
                    attempt=attempt,
                    stage=draft.stage,
                    role=draft.role,
                    round=draft.round,
                    schema_version=draft.schema_version,
                    prompt_version=draft.prompt_version,
                    generation_method=draft.generation_method.value,
                    generation_observations_json=[
                        item.model_dump(mode="json") for item in draft.generation_observations
                    ],
                    content_type=draft.content_type,
                    content_json=draft.content.model_dump(mode="json"),
                    content_hash=draft.content_hash,
                    created_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            payload = {
                "artifact_id": artifact_id,
                "attempt": attempt,
                "stage": draft.stage,
                "role": draft.role,
                "round": draft.round,
                "schema_version": draft.schema_version,
                "prompt_version": draft.prompt_version,
                "generation_method": draft.generation_method.value,
                "generation_observations": [
                    item.model_dump(mode="json") for item in draft.generation_observations
                ],
                "content_type": draft.content_type,
            }
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=attempt,
                    event_type="artifact.created",
                    node=draft.node,
                    payload_json=payload,
                    created_at=now,
                )
            )
            connection.commit()

        artifact = ResearchArtifact(
            id=artifact_id,
            run_id=run_id,
            attempt=attempt,
            stage=draft.stage,
            role=draft.role,
            round=draft.round,
            schema_version=draft.schema_version,
            prompt_version=draft.prompt_version,
            generation_method=draft.generation_method,
            generation_observations=draft.generation_observations,
            content=draft.content,
            created_at=_aware(now),
        )
        event = RunEvent(
            run_id=run_id,
            sequence=sequence,
            attempt=attempt,
            event_type="artifact.created",
            node=draft.node,
            payload=payload,
            created_at=_aware(now),
        )
        return artifact, event

    def list_artifacts(
        self,
        run_id: str,
        *,
        attempt: int | None = None,
    ) -> list[ResearchArtifact]:
        table = RunArtifactRecord.__table__
        stmt = select(table).where(table.c.run_id == run_id)
        if attempt is not None:
            stmt = stmt.where(table.c.attempt == attempt)
        stmt = stmt.order_by(table.c.created_at, table.c.id)
        with self.engine.connect() as connection:
            records = list(connection.execute(stmt).mappings())
        if not records and not self._run_exists(run_id):
            raise RunNotFoundError(run_id)
        return [self._artifact(record) for record in records]

    def seal_evidence(
        self,
        run_id: str,
        bundle: EvidenceBundle,
    ) -> tuple[EvidenceSealView, RunEvent | None]:
        """Persist the immutable evidence ledger and its event atomically."""
        now = _utc_naive()
        digest = bundle.digest
        if digest is None:  # pragma: no cover - enforced by EvidenceBundle
            raise ValueError("evidence bundle must have a digest")
        table = RunEvidenceRecord.__table__
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            run_row = connection.execute(
                select(
                    RunRecord.current_attempt,
                    RunRecord.status,
                ).where(RunRecord.id == run_id)
            ).first()
            if run_row is None:
                connection.rollback()
                raise RunNotFoundError(run_id)
            if run_row.status != RunStatus.RUNNING.value:
                connection.rollback()
                raise InvalidRunTransitionError(run_row.status)
            existing = (
                connection.execute(select(table).where(table.c.run_id == run_id)).mappings().first()
            )
            if existing is not None:
                if existing["digest"] != digest:
                    connection.rollback()
                    raise EvidenceConflictError("evidence seal replayed with a different digest")
                connection.commit()
                return self._evidence_view(existing), None

            attempt = run_row.current_attempt
            connection.execute(
                table.insert().values(
                    run_id=run_id,
                    sealed_attempt=attempt,
                    bundle_json=bundle.model_dump(mode="json"),
                    digest=digest,
                    item_count=len(bundle.items),
                    table_count=len(bundle.tables),
                    sealed_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1).where(
                    RunEventRecord.run_id == run_id
                )
            ).scalar_one()
            payload = {
                "attempt": attempt,
                "digest": digest,
                "item_count": len(bundle.items),
                "table_count": len(bundle.tables),
            }
            connection.execute(
                RunEventRecord.__table__.insert().values(
                    run_id=run_id,
                    sequence=sequence,
                    attempt=attempt,
                    event_type="evidence.sealed",
                    node="evidence.seal",
                    payload_json=payload,
                    created_at=now,
                )
            )
            connection.commit()
        view = EvidenceSealView(
            status="sealed",
            digest=digest,
            item_count=len(bundle.items),
            table_count=len(bundle.tables),
            sealed_attempt=attempt,
            sealed_at=_aware(now),
        )
        return (
            view,
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                attempt=attempt,
                event_type="evidence.sealed",
                node="evidence.seal",
                payload=payload,
                created_at=_aware(now),
            ),
        )

    def evidence_status(self, run_id: str) -> EvidenceSealView:
        with self.engine.connect() as connection:
            run_exists = connection.scalar(
                select(func.count()).select_from(RunRecord).where(RunRecord.id == run_id)
            )
            if not run_exists:
                raise RunNotFoundError(run_id)
            record = (
                connection.execute(
                    select(RunEvidenceRecord.__table__).where(RunEvidenceRecord.run_id == run_id)
                )
                .mappings()
                .first()
            )
        return (
            self._evidence_view(record)
            if record is not None
            else EvidenceSealView(status="pending")
        )

    def get_evidence(self, run_id: str) -> EvidenceBundle:
        with self.sessions() as session:
            record = session.get(RunEvidenceRecord, run_id)
            if record is None:
                if session.get(RunRecord, run_id) is None:
                    raise RunNotFoundError(run_id)
                raise EvidenceNotSealedError(run_id)
            return EvidenceBundle.model_validate(record.bundle_json)

    def complete(
        self,
        run_id: str,
        result: AnalysisResult,
        *,
        evidence: EvidenceBundle,
        benchmark: str,
        revision_draft: ResearchRevisionDraft | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        with self.sessions.begin() as session:
            if revision_draft is not None:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            sealed = session.get(RunEvidenceRecord, run_id)
            if sealed is None:
                raise EvidenceNotSealedError(run_id)
            if sealed.digest != evidence.digest:
                raise EvidenceConflictError("completed result does not match the sealed evidence")
            if result.decision is not None:
                request = AnalysisRequest.model_validate(record.request_json)
                market = self.market_bucket(request.ticker)
                decision = DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=market,
                    asset_type=request.asset_type.value,
                    analysis_date=request.analysis_date,
                    rating=result.decision.rating.value,
                    confidence=result.decision.confidence,
                    decision_json=result.decision.model_dump(mode="json"),
                    numeric_audit_json=(
                        result.numeric_audit.model_dump(mode="json")
                        if result.numeric_audit is not None
                        else None
                    ),
                    created_at=now,
                )
                session.add(decision)
                session.flush()
                outcome = OutcomeRecord(
                    decision_id=decision.id,
                    status=OutcomeObservationStatus.PENDING.value,
                    benchmark=benchmark,
                    market_timezone=str(market_timezone(request.ticker)),
                    method_category=METHOD_CATEGORY,
                    method_version=METHOD_VERSION,
                    price_semantics=PRICE_SEMANTICS,
                    adjustment_semantics=ADJUSTMENT_SEMANTICS,
                    horizon_limit=HORIZON_LIMIT,
                    limitations_json=list(OBSERVATION_LIMITATIONS),
                    holding_intervals=5,
                    next_check_at=max(
                        now,
                        earliest_outcome_check_at(
                            ticker=request.ticker,
                            analysis_date=request.analysis_date,
                            holding_intervals=5,
                        ).replace(tzinfo=None),
                    ),
                )
                session.add(outcome)
            record.status = RunStatus.SUCCEEDED.value
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.SUCCEEDED.value
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, result.metrics)
            if record.research_chain_requested:
                if revision_draft is None:
                    raise ValueError("explicit Research Chain execution requires a Revision draft")
                revision_id = self._create_initial_revision(
                    session,
                    record=record,
                    draft=revision_draft,
                    metrics=aggregate,
                    created_at=now,
                )
                session.flush()
                if result.decision is not None:
                    outcome.research_revision_id = revision_id
            elif record.research_chain_id is not None:
                if revision_draft is None:
                    raise ValueError("Research Chain update requires a Revision draft")
                revision_id = self._advance_research_chain(
                    session,
                    record=record,
                    draft=revision_draft,
                    metrics=aggregate,
                    created_at=now,
                )
                session.flush()
                if result.decision is not None:
                    outcome.research_revision_id = revision_id
            elif revision_draft is not None:
                raise ValueError("ordinary runs cannot create a Research Revision")
        return aggregate

    def fail(
        self,
        run_id: str,
        error: BaseException,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        code = type(error).__name__[:80]
        message = _sanitize_text(str(error)) or code
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.status = RunStatus.FAILED.value
            record.error_code = code
            record.error_message = message
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.FAILED.value
            attempt.error_code = code
            attempt.error_message = message
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, metrics)
        return aggregate

    def finish_cancel(
        self,
        run_id: str,
        *,
        metrics: RunMetrics | None = None,
    ) -> RunMetrics:
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            record.status = RunStatus.CANCELLED.value
            record.finished_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            attempt = self._attempt(session, record)
            attempt.status = RunStatus.CANCELLED.value
            attempt.finished_at = now
            attempt.lease_owner = None
            attempt.lease_expires_at = None
            aggregate = self._merge_metrics(record, attempt, metrics)
        return aggregate

    def get_result(self, run_id: str) -> AnalysisResult:
        view = self.get_run(run_id)
        artifacts = self.list_artifacts(run_id)
        with self.sessions() as session:
            decision_record = session.scalar(
                select(DecisionRecord).where(DecisionRecord.run_id == run_id)
            )
            evidence_record = session.get(RunEvidenceRecord, run_id)
        reports = order_reports(
            {
                artifact.role: artifact.content
                for artifact in artifacts
                if artifact.stage == "analyst" and isinstance(artifact.content, AnalystReport)
            }
        )
        decision = (
            ResearchDecision.model_validate(decision_record.decision_json)
            if decision_record
            else next(
                (
                    artifact.content
                    for artifact in reversed(artifacts)
                    if artifact.stage == "decision"
                    and isinstance(artifact.content, ResearchDecision)
                ),
                None,
            )
        )
        numeric_audit = (
            DecisionNumericAuditAppendix.model_validate(decision_record.numeric_audit_json)
            if decision_record and decision_record.numeric_audit_json
            else None
        )
        evidence = (
            EvidenceBundle.model_validate(evidence_record.bundle_json) if evidence_record else None
        )
        warnings = tuple(
            dict.fromkeys(
                (
                    *(
                        warning
                        for report in reports.values()
                        if isinstance(report, AnalystReport)
                        for warning in report.warnings
                    ),
                    *(
                        (
                            ResearchWarning(
                                code=(
                                    f"decision.numeric_audit_{decision.numeric_audit_status.value}"
                                ),
                                message=(_numeric_audit_warning_message(numeric_audit)),
                                source="committee.final.serialize.numeric",
                            ),
                        )
                        if decision is not None
                        and decision.numeric_audit_status
                        in {
                            NumericAuditStatus.PARTIAL,
                            NumericAuditStatus.INCOMPLETE,
                        }
                        else ()
                    ),
                )
            )
        )
        return AnalysisResult(
            run_id=run_id,
            status=view.status,
            instrument=view.request.ticker,
            instrument_name=view.instrument_name,
            instrument_local_name=view.instrument_local_name,
            reports=reports,
            decision=decision,
            numeric_audit=numeric_audit,
            evidence=evidence,
            metrics=view.metrics,
            recoveries=self.list_recoveries(run_id),
            warnings=warnings,
        )

    def list_attempts(self, run_id: str) -> tuple[RunAttemptView, ...]:
        if not self._run_exists(run_id):
            raise RunNotFoundError(run_id)
        with self.sessions() as session:
            records = tuple(
                session.scalars(
                    select(RunAttemptRecord)
                    .where(RunAttemptRecord.run_id == run_id)
                    .order_by(RunAttemptRecord.attempt)
                )
            )
        return tuple(
            RunAttemptView(
                attempt=record.attempt,
                status=RunStatus(record.status),
                resume_count=record.resume_count,
                metrics=RunMetrics.model_validate(record.metrics_json or {}),
                started_at=_aware(record.started_at),
                finished_at=_aware(record.finished_at),
                error_code=record.error_code,
            )
            for record in records
        )

    def _run_exists(self, run_id: str) -> bool:
        with self.engine.connect() as connection:
            return (
                connection.execute(select(RunRecord.id).where(RunRecord.id == run_id)).first()
                is not None
            )

    @staticmethod
    def _artifact(record: Any) -> ResearchArtifact:
        content_models = {
            "analyst_report": AnalystReport,
            "decision_brief": DecisionBrief,
            "research_case": ResearchCase,
            "debate_agenda": DebateAgenda,
            "rebuttal_review": RebuttalReview,
            "judge_draft": JudgeDraft,
            "risk_review": RiskReview,
            "research_decision": ResearchDecision,
        }
        model = content_models.get(record["content_type"])
        if model is None:
            raise ValueError(f"unsupported research artifact type: {record['content_type']}")
        generation_method = ArtifactGenerationMethod(record["generation_method"])
        generation_observations = tuple(
            ArtifactGenerationObservation.model_validate(item)
            for item in (record["generation_observations_json"] or ())
        )
        content = model.model_validate(record["content_json"])
        return ResearchArtifact(
            id=record["id"],
            run_id=record["run_id"],
            attempt=record["attempt"],
            stage=record["stage"],
            role=record["role"],
            round=record["round"],
            schema_version=record["schema_version"],
            prompt_version=record["prompt_version"],
            generation_method=generation_method,
            generation_observations=generation_observations,
            content=content,
            created_at=_aware(record["created_at"]),
        )

    @staticmethod
    def _evidence_view(record: Any) -> EvidenceSealView:
        return EvidenceSealView(
            status="sealed",
            digest=record["digest"],
            item_count=record["item_count"],
            table_count=record["table_count"],
            sealed_attempt=record["sealed_attempt"],
            sealed_at=_aware(record["sealed_at"]),
        )

    def pending_outcomes(
        self,
        limit: int = 20,
        *,
        due_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        due = due_at or _utc_naive()
        if due.tzinfo is not None:
            due = due.astimezone(timezone.utc).replace(tzinfo=None)
        stmt = (
            select(OutcomeRecord, DecisionRecord, ReflectionRecord)
            .join(DecisionRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .outerjoin(ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id)
            .where(
                RunRecord.trashed_at.is_(None),
                or_(
                    and_(
                        OutcomeRecord.status == OutcomeObservationStatus.PENDING.value,
                        OutcomeRecord.next_check_at.is_not(None),
                        OutcomeRecord.next_check_at <= due,
                    ),
                    and_(
                        OutcomeRecord.status == OutcomeObservationStatus.RESOLVED.value,
                        or_(
                            ReflectionRecord.status == OutcomeReflectionStatus.PENDING.value,
                            and_(
                                ReflectionRecord.status
                                == OutcomeReflectionStatus.RETRYABLE_FAILURE.value,
                                ReflectionRecord.next_retry_at.is_not(None),
                                ReflectionRecord.next_retry_at <= due,
                            ),
                        ),
                    ),
                ),
            )
            .order_by(
                func.coalesce(OutcomeRecord.next_check_at, ReflectionRecord.next_retry_at),
                DecisionRecord.analysis_date,
            )
            .limit(limit)
        )
        with self.sessions() as session:
            return [
                {
                    "outcome_id": outcome.id,
                    "decision_id": decision.id,
                    "ticker": decision.ticker,
                    "analysis_date": decision.analysis_date,
                    "benchmark": outcome.benchmark,
                    "holding_intervals": outcome.holding_intervals,
                    "decision": decision.decision_json,
                    "status": outcome.status,
                    "observation_start": outcome.observation_start,
                    "observation_end": outcome.observation_end,
                    "raw_return": outcome.raw_return,
                    "alpha_return": outcome.alpha_return,
                    "market_timezone": outcome.market_timezone,
                    "reflection_status": reflection.status if reflection else None,
                    "next_check_at": _aware(
                        outcome.next_check_at
                        if outcome.status == OutcomeObservationStatus.PENDING.value
                        else reflection.next_retry_at if reflection else None
                    ),
                }
                for outcome, decision, reflection in session.execute(stmt)
            ]

    def mark_outcome_checked(
        self,
        outcome_id: int,
        *,
        checked_at: datetime,
        next_check_at: datetime,
        error_message: str | None = None,
    ) -> None:
        if checked_at.tzinfo is not None:
            checked_at = checked_at.astimezone(timezone.utc).replace(tzinfo=None)
        if next_check_at.tzinfo is not None:
            next_check_at = next_check_at.astimezone(timezone.utc).replace(tzinfo=None)
        with self.sessions.begin() as session:
            outcome = session.get(OutcomeRecord, outcome_id)
            if outcome is None:
                return
            outcome.last_checked_at = checked_at
            outcome.next_check_at = next_check_at
            outcome.error_message = _sanitize_text(error_message)

    def resolve_outcome(
        self,
        outcome_id: int,
        *,
        observation_start,
        observation_end,
        raw_return: float,
        alpha_return: float,
        reflection: str,
    ) -> None:
        now = _aware(_utc_naive())
        from .outcomes import OutcomeObservation

        self.persist_outcome_observation(
            outcome_id,
            observation=OutcomeObservation(
                raw_return=raw_return,
                alpha_return=alpha_return,
                holding_intervals=5,
                start_date=observation_start,
                end_date=observation_end,
            ),
            observed_at=now,
        )
        self.persist_generated_reflection(
            outcome_id,
            reflection=reflection,
            generated_at=now,
            allow_legacy_unstructured=True,
        )

    def persist_outcome_observation(
        self,
        outcome_id: int,
        *,
        observation: Any,
        observed_at: datetime,
    ) -> None:
        observed = observed_at
        if observed.tzinfo is not None:
            observed = observed.astimezone(timezone.utc).replace(tzinfo=None)
        with self.sessions.begin() as session:
            outcome = session.scalar(
                select(OutcomeRecord)
                .join(
                    DecisionRecord,
                    OutcomeRecord.decision_id == DecisionRecord.id,
                )
                .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
                .where(
                    OutcomeRecord.id == outcome_id,
                    RunRecord.trashed_at.is_(None),
                )
            )
            if outcome is None or outcome.status == OutcomeObservationStatus.RESOLVED.value:
                return
            outcome.status = OutcomeObservationStatus.RESOLVED.value
            outcome.observation_start = observation.start_date
            outcome.observation_end = observation.end_date
            outcome.holding_intervals = observation.holding_intervals
            outcome.raw_return = observation.raw_return
            outcome.alpha_return = observation.alpha_return
            outcome.last_checked_at = observed
            outcome.next_check_at = None
            outcome.resolved_at = observed
            outcome.data_available_at = observed
            outcome.error_message = None
            session.add(
                ReflectionRecord(
                    outcome_id=outcome.id,
                    status=OutcomeReflectionStatus.PENDING.value,
                    text=None,
                    created_at=observed,
                )
            )

    def start_outcome_reflection_attempt(
        self,
        outcome_id: int,
        *,
        started_at: datetime,
        trigger: str = "outcome_settlement",
        origin: str = "automatic",
        attempt_kind: str = "initial",
    ) -> dict[str, int | str] | None:
        """Reserve the sole active generation cycle before invoking an LLM."""
        started = (
            started_at.astimezone(timezone.utc).replace(tzinfo=None)
            if started_at.tzinfo is not None
            else started_at
        )
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if reflection is None or reflection.status == OutcomeReflectionStatus.GENERATED.value:
                return None
            existing = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.status.in_(("queued", "running")),
                )
            )
            if existing is not None:
                if (
                    existing.status != "queued"
                    or (existing.due_at is not None and existing.due_at > started)
                ):
                    return None
                cycle = existing
                cycle.status = "running"
                cycle.started_at = started
            else:
                if reflection.status != OutcomeReflectionStatus.PENDING.value:
                    return None
                cycle = ReflectionGenerationCycleRecord(
                    id=str(uuid4()),
                    outcome_id=outcome_id,
                    status="running",
                    trigger=trigger,
                    origin=origin,
                    retry_ordinal=0,
                    queued_at=started,
                    due_at=started,
                    started_at=started,
                )
                try:
                    with session.begin_nested():
                        session.add(cycle)
                        session.flush()
                except IntegrityError:
                    return None
            sequence = (
                session.scalar(
                    select(func.count())
                    .select_from(ReflectionAttemptRecord)
                    .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                )
                or 0
            ) + 1
            attempt = ReflectionAttemptRecord(
                reflection_id=reflection.id,
                generation_cycle_id=cycle.id,
                sequence=sequence,
                trigger=cycle.trigger,
                origin=cycle.origin,
                attempt_kind=attempt_kind,
                started_at=started,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            session.flush()
            reflection.current_generation_cycle_id = cycle.id
            reflection.last_attempted_at = started
            return {"cycle_id": cycle.id, "attempt_id": attempt.id}

    def start_outcome_reflection_repair_attempt(
        self,
        outcome_id: int,
        *,
        attempt_ids: dict[str, int | str],
        started_at: datetime,
    ) -> dict[str, int | str]:
        """Append the sole permitted schema repair to an active generation cycle."""
        started = (
            started_at.astimezone(timezone.utc).replace(tzinfo=None)
            if started_at.tzinfo is not None
            else started_at
        )
        with self.sessions.begin() as session:
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if reflection is None:
                raise ValueError("Outcome Reflection is missing")
            cycle = session.get(
                ReflectionGenerationCycleRecord, attempt_ids["cycle_id"]
            )
            if cycle is None or cycle.outcome_id != outcome_id or cycle.status != "running":
                raise ValueError("Outcome Reflection generation cycle is not active")
            sequence = (
                session.scalar(
                    select(func.count())
                    .select_from(ReflectionAttemptRecord)
                    .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                )
                or 0
            ) + 1
            attempt = ReflectionAttemptRecord(
                reflection_id=reflection.id,
                generation_cycle_id=cycle.id,
                sequence=sequence,
                trigger=cycle.trigger,
                origin=cycle.origin,
                attempt_kind="repair",
                started_at=started,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            reflection.last_attempted_at = started
            session.flush()
            return {"cycle_id": cycle.id, "attempt_id": attempt.id}

    @staticmethod
    def _finish_reflection_attempt(
        session: Session,
        *,
        reflection: ReflectionRecord,
        outcome_id: int,
        finished_at: datetime,
        attempt_ids: dict[str, int | str] | None,
        result: str,
        diagnostics: dict[str, str] | None = None,
        invalid_candidate: str | None = None,
        invalid_candidate_digest: str | None = None,
        invalid_candidate_length: int | None = None,
        validation_issues: list[str] | None = None,
        wall_time_seconds: float | None = None,
        schema_version: str = "outcome_reflection_legacy_unstructured.v1",
        finish_cycle: bool = True,
        usage: dict[str, int | float | None] | None = None,
    ) -> ReflectionAttemptRecord:
        if attempt_ids is None:
            cycle = ReflectionGenerationCycleRecord(
                id=str(uuid4()),
                outcome_id=outcome_id,
                status="running",
                trigger="repository_write",
                origin="automatic",
                retry_ordinal=0,
                queued_at=finished_at,
                started_at=finished_at,
            )
            session.add(cycle)
            session.flush()
            sequence = (
                session.scalar(
                    select(func.count())
                    .select_from(ReflectionAttemptRecord)
                    .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                )
                or 0
            ) + 1
            attempt = ReflectionAttemptRecord(
                reflection_id=reflection.id,
                generation_cycle_id=cycle.id,
                sequence=sequence,
                trigger="repository_write",
                origin="automatic",
                attempt_kind="unstructured",
                started_at=finished_at,
                usage_status="not_reported",
                llm_calls=1,
            )
            session.add(attempt)
            reflection.current_generation_cycle_id = cycle.id
        else:
            attempt = session.get(ReflectionAttemptRecord, attempt_ids["attempt_id"])
            if attempt is None or attempt.reflection_id != reflection.id:
                raise ValueError("Reflection Attempt does not belong to Outcome")
            cycle = session.get(
                ReflectionGenerationCycleRecord, attempt_ids["cycle_id"]
            )
            if cycle is None:
                raise ValueError("Reflection generation cycle is missing")
        candidate, digest, length = _invalid_candidate_audit(invalid_candidate)
        if invalid_candidate_digest is not None:
            digest = invalid_candidate_digest
        if invalid_candidate_length is not None:
            length = invalid_candidate_length
        attempt.finished_at = finished_at
        attempt.outcome = result
        attempt.candidate_schema_version = schema_version
        attempt.diagnostics_json = diagnostics
        attempt.wall_time_seconds = wall_time_seconds
        attempt.invalid_candidate = candidate
        attempt.invalid_candidate_digest = digest
        attempt.invalid_candidate_length = length
        attempt.validation_issues_json = validation_issues
        if usage and any(value is not None for value in usage.values()):
            attempt.usage_status = "reported"
            attempt.input_tokens = _usage_int(usage.get("input_tokens"))
            attempt.output_tokens = _usage_int(usage.get("output_tokens"))
            attempt.cache_hit_input_tokens = _usage_int(
                usage.get("cache_hit_input_tokens")
            )
            attempt.cache_miss_input_tokens = _usage_int(
                usage.get("cache_miss_input_tokens")
            )
            attempt.reasoning_output_tokens = _usage_int(
                usage.get("reasoning_output_tokens")
            )
            cost = usage.get("provider_reported_cost_usd")
            attempt.provider_reported_cost_usd = (
                float(cost) if isinstance(cost, (int, float)) and cost >= 0 else None
            )
        if finish_cycle:
            cycle.status = {"generated": "succeeded", "invalid": "invalid"}.get(
                result, "failed"
            )
            cycle.finished_at = finished_at
        return attempt

    def mark_reflection_failure(
        self,
        outcome_id: int,
        *,
        attempted_at: datetime,
        next_retry_at: datetime | None = None,
        error_code: str,
        attempt_ids: dict[str, int | str] | None = None,
        wall_time_seconds: float | None = None,
    ) -> None:
        attempted = (
            attempted_at.astimezone(timezone.utc).replace(tzinfo=None)
            if attempted_at.tzinfo is not None
            else attempted_at
        )
        retry = (
            next_retry_at.astimezone(timezone.utc).replace(tzinfo=None)
            if next_retry_at is not None and next_retry_at.tzinfo is not None
            else next_retry_at
        )
        with self.sessions.begin() as session:
            reflection = session.scalar(
                select(ReflectionRecord).where(ReflectionRecord.outcome_id == outcome_id)
            )
            if (
                reflection is None
                or reflection.status == OutcomeReflectionStatus.GENERATED.value
            ):
                return
            reflection.status = OutcomeReflectionStatus.RETRYABLE_FAILURE.value
            reflection.last_attempted_at = attempted
            reflection.error_code = _sanitize_text(error_code, limit=80)
            self._finish_reflection_attempt(
                session,
                reflection=reflection,
                outcome_id=outcome_id,
                finished_at=attempted,
                attempt_ids=attempt_ids,
                result="provider_failure",
                diagnostics={"error_code": reflection.error_code or "unknown"},
                wall_time_seconds=wall_time_seconds,
            )
            cycle = (
                session.get(ReflectionGenerationCycleRecord, attempt_ids["cycle_id"])
                if attempt_ids is not None
                else None
            )
            if (
                cycle is not None
                and cycle.origin == "automatic"
                and cycle.retry_ordinal < len(_REFLECTION_RETRY_DELAYS)
            ):
                retry = attempted + _REFLECTION_RETRY_DELAYS[cycle.retry_ordinal]
                scheduled = ReflectionGenerationCycleRecord(
                    id=str(uuid4()),
                    outcome_id=outcome_id,
                    status="queued",
                    trigger="outcome_settlement",
                    origin="automatic",
                    retry_ordinal=cycle.retry_ordinal + 1,
                    queued_at=attempted,
                    due_at=retry,
                )
                session.add(scheduled)
                reflection.current_generation_cycle_id = scheduled.id
                reflection.next_retry_at = retry
            else:
                reflection.next_retry_at = None

    def persist_generated_reflection(
        self,
        outcome_id: int,
        *,
        reflection: str | None = None,
        draft: OutcomeReflectionDraft | None = None,
        generated_at: datetime,
        allow_legacy_unstructured: bool = False,
        attempt_ids: dict[str, int | str] | None = None,
        wall_time_seconds: float | None = None,
        terminal_invalid: bool = True,
        validation_issues: list[str] | None = None,
        usage: dict[str, int | float | None] | None = None,
        invalid_candidate_digest: str | None = None,
        invalid_candidate_length: int | None = None,
    ) -> str | None:
        generated = generated_at
        if generated.tzinfo is not None:
            generated = generated.astimezone(timezone.utc).replace(tzinfo=None)
        raw_candidate = reflection if isinstance(reflection, str) else None
        text = draft.readable_text if draft is not None else (reflection or "").strip()
        with self.sessions.begin() as session:
            row = session.execute(
                select(
                    ReflectionRecord,
                    OutcomeRecord,
                    DecisionRecord,
                    ResearchRevisionRecord,
                )
                .join(OutcomeRecord, OutcomeRecord.id == ReflectionRecord.outcome_id)
                .join(DecisionRecord, DecisionRecord.id == OutcomeRecord.decision_id)
                .outerjoin(
                    ResearchRevisionRecord,
                    ResearchRevisionRecord.id == OutcomeRecord.research_revision_id,
                )
                .where(OutcomeRecord.id == outcome_id)
            ).first()
            if row is None:
                return None
            reflection_record, outcome, decision, revision = row
            if reflection_record.status == OutcomeReflectionStatus.GENERATED.value:
                return OutcomeReflectionStatus.GENERATED.value
            reflection_record.last_attempted_at = generated
            reflection_record.next_retry_at = None
            reflection_record.error_code = None
            structural_issues = list(validation_issues or ())
            if not text:
                structural_issues.append("empty_candidate")
            if len(text) > 12_000:
                structural_issues.append("candidate_too_long")
            if not allow_legacy_unstructured and draft is None:
                structural_issues.append("missing_structured_draft")
            if structural_issues:
                reflection_record.status = OutcomeReflectionStatus.INVALID.value
                reflection_record.text = None
                reflection_record.candidate_json = None
                self._finish_reflection_attempt(
                    session,
                    reflection=reflection_record,
                    outcome_id=outcome_id,
                    finished_at=generated,
                    attempt_ids=attempt_ids,
                    result="invalid",
                    invalid_candidate=raw_candidate,
                    invalid_candidate_digest=invalid_candidate_digest,
                    invalid_candidate_length=invalid_candidate_length,
                    validation_issues=list(dict.fromkeys(structural_issues)),
                    wall_time_seconds=wall_time_seconds,
                    schema_version=OUTCOME_REFLECTION_SCHEMA_VERSION,
                    finish_cycle=terminal_invalid,
                    usage=usage,
                )
                if not terminal_invalid:
                    reflection_record.status = OutcomeReflectionStatus.PENDING.value
                return OutcomeReflectionStatus.INVALID.value
            if (
                outcome.data_available_at is None
                or outcome.observation_start is None
                or outcome.observation_end is None
            ):
                raise ValueError("Outcome Observation is incomplete")
            qualification_started_at = max(_utc_naive(), outcome.data_available_at, generated)
            qualification = qualify_reflection(
                source=FeedbackSource(
                    decision_id=decision.id,
                    revision_id=outcome.research_revision_id,
                    decision_rating=decision.rating,
                    decision_thesis=str(decision.decision_json.get("thesis") or ""),
                    decision_cutoff=decision.analysis_date,
                    revision_cutoff=revision.cutoff if revision is not None else None,
                    ticker=decision.ticker,
                    market=decision.market,
                ),
                observation=ObservationQualificationInput(
                    start=outcome.observation_start,
                    end=outcome.observation_end,
                    data_available_at=outcome.data_available_at,
                    method_category=outcome.method_category,
                    horizon_limit=outcome.horizon_limit,
                ),
                reflection=ReflectionQualificationInput(
                    method_lesson=(
                        draft.method_lesson
                        if draft is not None
                        else reflection_candidate_lesson(text) or ""
                    ),
                    generated_at=generated,
                ),
                qualified_at=qualification_started_at,
            )
            qualified_at = max(_utc_naive(), qualification_started_at)
            reflection_record.status = OutcomeReflectionStatus.GENERATED.value
            reflection_record.text = text
            reflection_record.candidate_json = (
                draft.audit_candidate() if draft is not None else qualification.candidate
            )
            reflection_record.generated_at = generated
            available_at = max(outcome.data_available_at, generated, qualified_at)
            session.add(
                OutcomeFeedbackRecord(
                    reflection_id=reflection_record.id,
                    status=qualification.status.value,
                    qualification_policy_version=(
                        "outcome_feedback_qualification.v1"
                        if allow_legacy_unstructured
                        else qualification.qualification_policy_version
                    ),
                    reasons_json=list(qualification.reasons),
                    method_category=outcome.method_category,
                    horizon_limit=outcome.horizon_limit,
                    applicability_json=qualification.applicability,
                    qualified_at=qualified_at,
                    available_at=available_at,
                )
            )
            attempt = self._finish_reflection_attempt(
                session,
                reflection=reflection_record,
                outcome_id=outcome_id,
                finished_at=generated,
                attempt_ids=attempt_ids,
                result="generated",
                wall_time_seconds=wall_time_seconds,
                schema_version=(
                    OUTCOME_REFLECTION_SCHEMA_VERSION
                    if draft is not None
                    else "outcome_reflection_legacy_unstructured.v1"
                ),
                usage=draft.usage if draft is not None else usage,
            )
            session.flush()
            reflection_record.successful_attempt_id = attempt.id
            return OutcomeReflectionStatus.GENERATED.value

    def enqueue_outcome_reflection_regeneration(
        self,
        outcome_id: int,
        *,
        idempotency_key: str,
        queued_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create or replay the one manual Reflection cycle allowed by the lifecycle."""
        key = idempotency_key.strip()
        if not key:
            raise OutcomeReflectionRegenerationConflictError(
                "Idempotency-Key is required"
            )
        if len(key) > 200:
            raise OutcomeReflectionRegenerationConflictError(
                "Idempotency-Key is too long"
            )
        queued = queued_at or _aware(_utc_naive())
        if queued.tzinfo is not None:
            queued = queued.astimezone(timezone.utc).replace(tzinfo=None)
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.execute(
                select(OutcomeRecord, ReflectionRecord, OutcomeFeedbackRecord)
                .outerjoin(
                    ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id
                )
                .outerjoin(
                    OutcomeFeedbackRecord,
                    OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
                )
                .where(OutcomeRecord.id == outcome_id)
            ).first()
            if row is None:
                raise OutcomeReflectionRegenerationNotFoundError(str(outcome_id))
            outcome, reflection, feedback = row
            review_status = derive_review_status(
                outcome_status=outcome.status,
                outcome_error=outcome.error_message,
                reflection_status=reflection.status if reflection else None,
                reflection_next_retry_at=reflection.next_retry_at if reflection else None,
                feedback_status=feedback.status if feedback else None,
            )
            existing_key = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.idempotency_key == key,
                )
            )
            if existing_key is not None:
                return {
                    "cycle": self._generation_cycle_view(existing_key),
                    "review_status": review_status,
                    "reflection_status": reflection.status if reflection else None,
                }
            if review_status == "lifecycle_inconsistent":
                raise OutcomeReflectionRegenerationConflictError(
                    "Review lifecycle is inconsistent"
                )
            active = session.scalar(
                select(ReflectionGenerationCycleRecord).where(
                    ReflectionGenerationCycleRecord.outcome_id == outcome_id,
                    ReflectionGenerationCycleRecord.status.in_(("queued", "running")),
                )
            )
            if active is not None:
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection generation is already active",
                    active_cycle_id=active.id,
                )
            if (
                outcome.status != OutcomeObservationStatus.RESOLVED.value
                or reflection is None
                or reflection.status == OutcomeReflectionStatus.GENERATED.value
                or feedback is not None
            ):
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection cannot be regenerated from this lifecycle state"
                )
            retry_exhausted = (
                reflection.status == OutcomeReflectionStatus.RETRYABLE_FAILURE.value
                and reflection.next_retry_at is None
            )
            if reflection.status != OutcomeReflectionStatus.INVALID.value and not retry_exhausted:
                raise OutcomeReflectionRegenerationConflictError(
                    "Outcome Reflection is not eligible for regeneration"
                )
            cycle = ReflectionGenerationCycleRecord(
                id=str(uuid4()),
                outcome_id=outcome_id,
                status="queued",
                trigger="user_regeneration",
                origin="manual",
                retry_ordinal=0,
                idempotency_key=key,
                queued_at=queued,
                due_at=queued,
            )
            session.add(cycle)
            reflection.status = OutcomeReflectionStatus.PENDING.value
            reflection.next_retry_at = None
            reflection.error_code = None
            reflection.current_generation_cycle_id = cycle.id
            session.flush()
            return {
                "cycle": self._generation_cycle_view(cycle),
                "review_status": derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status if feedback else None,
                ),
                "reflection_status": reflection.status,
            }

    @staticmethod
    def _generation_cycle_view(cycle: ReflectionGenerationCycleRecord) -> dict[str, Any]:
        return {
            "id": cycle.id,
            "outcome_id": cycle.outcome_id,
            "status": cycle.status,
            "origin": cycle.origin,
            "trigger": cycle.trigger,
            "retry_ordinal": cycle.retry_ordinal,
            "queued_at": _aware(cycle.queued_at),
            "due_at": _aware(cycle.due_at),
        }

    def retry_outcome_reflection(self, outcome_id: int) -> bool:
        """Deprecated compatibility adapter for one release cycle."""
        try:
            self.enqueue_outcome_reflection_regeneration(
                outcome_id,
                idempotency_key=f"legacy-reflection-retry:{outcome_id}",
            )
        except OutcomeReflectionRegenerationNotFoundError:
            return True
        except OutcomeReflectionRegenerationConflictError:
            return False
        return True

    def retire_outcome_feedback(
        self,
        feedback_id: int,
        *,
        reason: OutcomeFeedbackRetirementReason,
        note: str | None,
    ) -> dict[str, Any]:
        with self.sessions.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.execute(
                select(OutcomeRecord, ReflectionRecord, OutcomeFeedbackRecord)
                .join(ReflectionRecord, ReflectionRecord.outcome_id == OutcomeRecord.id)
                .join(
                    OutcomeFeedbackRecord,
                    OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
                )
                .where(OutcomeFeedbackRecord.id == feedback_id)
            ).first()
            if row is None:
                raise OutcomeFeedbackRetirementNotFoundError(str(feedback_id))
            outcome, reflection, feedback = row
            if (
                derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status,
                )
                == "lifecycle_inconsistent"
            ):
                raise OutcomeFeedbackRetirementConflictError(
                    "Review lifecycle is inconsistent"
                )
            if feedback.status == OutcomeFeedbackStatus.RETIRED.value:
                return {
                    **self._outcome_feedback_retirement_view(feedback),
                    "review_status": derive_review_status(
                        outcome_status=outcome.status,
                        outcome_error=outcome.error_message,
                        reflection_status=reflection.status,
                        reflection_next_retry_at=reflection.next_retry_at,
                        feedback_status=feedback.status,
                    ),
                }
            if feedback.status != OutcomeFeedbackStatus.ELIGIBLE.value:
                raise OutcomeFeedbackRetirementConflictError(
                    "Outcome Feedback is not eligible for retirement"
                )
            feedback.status = OutcomeFeedbackStatus.RETIRED.value
            feedback.retirement_reason = reason.value
            feedback.retirement_note = note
            feedback.retired_at = _utc_naive()
            session.flush()
            return {
                **self._outcome_feedback_retirement_view(feedback),
                "review_status": derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status,
                    reflection_next_retry_at=reflection.next_retry_at,
                    feedback_status=feedback.status,
                ),
            }

    @staticmethod
    def _outcome_feedback_retirement_view(
        feedback: OutcomeFeedbackRecord,
    ) -> dict[str, Any]:
        return {
            "status": feedback.status,
            "retirement_reason": feedback.retirement_reason,
            "retirement_note": feedback.retirement_note,
            "retired_at": _aware(feedback.retired_at),
        }

    def memory_context(
        self,
        ticker: str,
        asset_type: str,
        *,
        same_limit: int = 5,
        cross_limit: int = 3,
    ) -> MemoryContext:
        market = self.market_bucket(ticker)
        resolved = (
            select(
                DecisionRecord,
                OutcomeRecord,
                ReflectionRecord,
            )
            .join(OutcomeRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(
                ReflectionRecord,
                ReflectionRecord.outcome_id == OutcomeRecord.id,
            )
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .where(
                RunRecord.trashed_at.is_(None),
                OutcomeRecord.status == OutcomeObservationStatus.RESOLVED.value,
                OutcomeRecord.holding_intervals >= 5,
                OutcomeRecord.raw_return.is_not(None),
                OutcomeRecord.alpha_return.is_not(None),
                ReflectionRecord.status == OutcomeReflectionStatus.GENERATED.value,
                ReflectionRecord.text.is_not(None),
            )
            .order_by(
                OutcomeRecord.resolved_at.desc(),
                OutcomeRecord.id.desc(),
            )
        )
        with self.sessions() as session:
            rows = list(session.execute(resolved))
        same: list[MemoryRecord] = []
        cross: list[MemoryRecord] = []
        ticker_key = ticker.casefold()
        asset_type_key = asset_type.casefold()
        for decision_record, outcome_record, reflection_record in rows:
            reflection = (reflection_record.text or "").strip()
            if not reflection:
                continue
            try:
                decision = ResearchDecision.model_validate(decision_record.decision_json)
                outcome = MemoryOutcome(
                    benchmark=outcome_record.benchmark,
                    observation_start=outcome_record.observation_start,
                    observation_end=outcome_record.observation_end,
                    holding_intervals=outcome_record.holding_intervals,
                    raw_return=outcome_record.raw_return,
                    alpha_return=outcome_record.alpha_return,
                )
            except ValueError:
                continue
            if decision_record.ticker.casefold() == ticker_key and len(same) < max(0, same_limit):
                same.append(
                    MemoryRecord(
                        ref=f"memory:{decision_record.run_id}",
                        run_id=decision_record.run_id,
                        scope="same_ticker",
                        ticker=decision_record.ticker,
                        market=decision_record.market,
                        analysis_date=decision_record.analysis_date,
                        decision=decision,
                        outcome=outcome,
                        reflection=reflection,
                    )
                )
            elif (
                decision_record.ticker.casefold() != ticker_key
                and decision_record.asset_type.casefold() == asset_type_key
                and market is not None
                and decision_record.market == market
                and len(cross) < max(0, cross_limit)
            ):
                cross.append(
                    MemoryRecord(
                        ref=f"memory:{decision_record.run_id}",
                        run_id=decision_record.run_id,
                        scope="same_market",
                        ticker=decision_record.ticker,
                        market=decision_record.market,
                        analysis_date=decision_record.analysis_date,
                        reflection=reflection,
                    )
                )
        return MemoryContext(
            instrument=ticker,
            market=market,
            items=(*same, *cross),
        )

    def review_entries(
        self,
        *,
        outcome_id: int | None = None,
        ticker: str | None = None,
        market: str | None = None,
        q: str | None = None,
        status_group: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = min(max(1, limit), 500)
        filters_by_derived_status = status_group not in {None, "", "all"}
        stmt = (
            select(
                DecisionRecord,
                OutcomeRecord,
                ReflectionRecord,
                OutcomeFeedbackRecord,
                RunRecord.instrument_name,
                RunRecord.instrument_local_name,
                RunRecord.request_json,
            )
            .join(OutcomeRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .outerjoin(
                ReflectionRecord,
                ReflectionRecord.outcome_id == OutcomeRecord.id,
            )
            .outerjoin(
                OutcomeFeedbackRecord,
                OutcomeFeedbackRecord.reflection_id == ReflectionRecord.id,
            )
            .order_by(
                DecisionRecord.created_at.desc(),
                DecisionRecord.id.desc(),
            )
            .where(RunRecord.trashed_at.is_(None))
        )
        if not filters_by_derived_status:
            stmt = stmt.limit(bounded_limit)
        if outcome_id is not None:
            stmt = stmt.where(OutcomeRecord.id == outcome_id)
        if ticker and (ticker_query := ticker.strip().casefold()):
            stmt = stmt.where(
                func.lower(DecisionRecord.ticker).contains(
                    ticker_query,
                    autoescape=True,
                )
            )
        if market and (market_query := market.strip().casefold()):
            stmt = stmt.where(
                func.lower(func.coalesce(DecisionRecord.market, "")).contains(
                    market_query,
                    autoescape=True,
                )
            )
        if q and (query := q.strip().casefold()):
            decision_fields = (
                "$.rating",
                "$.thesis",
                "$.catalysts",
                "$.risks",
                "$.invalidation_conditions",
                "$.time_horizon",
                "$.scenarios",
                "$.unresolved_questions",
            )
            stmt = stmt.where(
                or_(
                    func.lower(DecisionRecord.run_id).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(DecisionRecord.ticker).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(RunRecord.instrument_local_name, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(func.coalesce(DecisionRecord.market, "")).contains(
                        query,
                        autoescape=True,
                    ),
                    *(
                        func.lower(
                            func.coalesce(
                                func.json_extract(
                                    DecisionRecord.decision_json,
                                    path,
                                ),
                                "",
                            )
                        ).contains(
                            query,
                            autoescape=True,
                        )
                        for path in decision_fields
                    ),
                    func.lower(func.coalesce(ReflectionRecord.text, "")).contains(
                        query,
                        autoescape=True,
                    ),
                )
            )
        with self.sessions() as session:
            reviews = []
            for (
                decision,
                outcome,
                reflection,
                feedback,
                instrument_name,
                instrument_local_name,
                request_json,
            ) in session.execute(stmt):
                review_status = derive_review_status(
                    outcome_status=outcome.status,
                    outcome_error=outcome.error_message,
                    reflection_status=reflection.status if reflection else None,
                    reflection_next_retry_at=(
                        reflection.next_retry_at if reflection else None
                    ),
                    feedback_status=feedback.status if feedback else None,
                )
                if not review_status_in_group(review_status, status_group):
                    continue
                generation_cycle = (
                    session.get(
                        ReflectionGenerationCycleRecord,
                        reflection.current_generation_cycle_id,
                    )
                    if reflection and reflection.current_generation_cycle_id
                    else None
                )
                reviews.append(
                    {
                    "run_id": decision.run_id,
                    "outcome_id": outcome.id,
                    "review_status": review_status,
                    "lifecycle_actions_allowed": review_status != "lifecycle_inconsistent",
                    "ticker": decision.ticker,
                    "instrument_name": instrument_name,
                    "instrument_local_name": instrument_local_name,
                    "market": decision.market,
                    "asset_type": decision.asset_type,
                    "analysis_date": decision.analysis_date.isoformat(),
                    "profile": AnalysisRequest.model_validate(request_json).profile,
                    "decision": decision.decision_json,
                    "outcome": {
                        "status": outcome.status,
                        "source_decision_id": decision.id,
                        "source_revision_id": outcome.research_revision_id,
                        "benchmark": outcome.benchmark,
                        "market_timezone": outcome.market_timezone,
                        "method_category": outcome.method_category,
                        "method_version": outcome.method_version,
                        "price_semantics": outcome.price_semantics,
                        "adjustment_semantics": outcome.adjustment_semantics,
                        "horizon_limit": outcome.horizon_limit,
                        "limitations": outcome.limitations_json,
                        "observation_start": (
                            outcome.observation_start.isoformat()
                            if outcome.observation_start
                            else None
                        ),
                        "observation_end": (
                            outcome.observation_end.isoformat() if outcome.observation_end else None
                        ),
                        "holding_intervals": outcome.holding_intervals,
                        "raw_return": outcome.raw_return,
                        "alpha_return": outcome.alpha_return,
                        "resolved_at": _aware(outcome.resolved_at),
                        "data_available_at": _aware(outcome.data_available_at),
                        "last_checked_at": _aware(outcome.last_checked_at),
                        "next_check_at": _aware(outcome.next_check_at),
                        "error_message": outcome.error_message,
                    },
                    "method_feedback": (
                        reflection_candidate_lesson(
                            reflection.text or "", reflection.candidate_json
                        )
                        if review_status == "feedback_available" and reflection
                        else None
                    ),
                    "outcome_reflection": (
                        {
                            "status": reflection.status,
                            "created_at": _aware(reflection.created_at),
                            "generated_at": _aware(reflection.generated_at),
                            "last_attempted_at": _aware(reflection.last_attempted_at),
                            "next_retry_at": _aware(reflection.next_retry_at),
                            "error_code": reflection.error_code,
                            "generation_cycle": (
                                self._generation_cycle_view(generation_cycle)
                                if generation_cycle is not None else None
                            ),
                        }
                        if reflection
                        else None
                    ),
                    "outcome_feedback": (
                        {
                            "id": feedback.id,
                            "status": feedback.status,
                            "qualification_policy_version": (
                                feedback.qualification_policy_version
                            ),
                            "reasons": feedback.reasons_json,
                            "method_category": feedback.method_category,
                            "horizon_limit": feedback.horizon_limit,
                            "applicability": feedback.applicability_json,
                            "qualified_at": _aware(feedback.qualified_at),
                            "available_at": _aware(feedback.available_at),
                            "retirement_reason": feedback.retirement_reason,
                            "retirement_note": feedback.retirement_note,
                            "retired_at": _aware(feedback.retired_at),
                        }
                        if feedback
                        else None
                    ),
                }
                )
                if filters_by_derived_status and len(reviews) >= bounded_limit:
                    break
            return reviews

    def review_audit_detail(self, outcome_id: int) -> dict[str, Any] | None:
        """Return progressively disclosed audit-only Reflection provenance."""
        review = next(
            (
                item
                for item in self.review_entries(outcome_id=outcome_id, limit=1)
                if item["outcome_id"] == outcome_id
            ),
            None,
        )
        if review is None:
            return None
        with self.sessions() as session:
            reflection = session.scalar(
                select(ReflectionRecord)
                .join(OutcomeRecord, OutcomeRecord.id == ReflectionRecord.outcome_id)
                .join(DecisionRecord, DecisionRecord.id == OutcomeRecord.decision_id)
                .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
                .where(
                    OutcomeRecord.id == outcome_id,
                    RunRecord.trashed_at.is_(None),
                )
            )
            attempts = list(
                session.scalars(
                    select(ReflectionAttemptRecord)
                    .where(ReflectionAttemptRecord.reflection_id == reflection.id)
                    .order_by(ReflectionAttemptRecord.sequence, ReflectionAttemptRecord.id)
                )
            ) if reflection else []
        metric_names = (
            "llm_calls",
            "input_tokens",
            "output_tokens",
            "cache_hit_input_tokens",
            "cache_miss_input_tokens",
            "reasoning_output_tokens",
            "wall_time_seconds",
            "provider_reported_cost_usd",
        )
        aggregate = {
            name: (
                sum(getattr(attempt, name) for attempt in attempts)
                if attempts
                and all(getattr(attempt, name) is not None for attempt in attempts)
                else None
            )
            for name in metric_names
        }
        usage_status = (
            "legacy_unknown"
            if any(attempt.usage_status == "legacy_unknown" for attempt in attempts)
            else "not_reported"
            if any(attempt.usage_status == "not_reported" for attempt in attempts)
            else "reported"
            if attempts
            else "not_reported"
        )
        return {
            "review": review,
            "reflection": reflection.text if reflection else None,
            "attempts": [
                {
                    "id": attempt.id,
                    "generation_cycle_id": attempt.generation_cycle_id,
                    "sequence": attempt.sequence,
                    "trigger": attempt.trigger,
                    "origin": attempt.origin,
                    "attempt_kind": attempt.attempt_kind,
                    "started_at": _aware(attempt.started_at),
                    "finished_at": _aware(attempt.finished_at),
                    "outcome": attempt.outcome,
                    "attempt_schema_version": attempt.attempt_schema_version,
                    "candidate_schema_version": attempt.candidate_schema_version,
                    "diagnostics": attempt.diagnostics_json,
                    "usage": {name: getattr(attempt, name) for name in ("usage_status", *metric_names)},
                    "invalid_candidate": attempt.invalid_candidate,
                    "invalid_candidate_digest": attempt.invalid_candidate_digest,
                    "invalid_candidate_length": attempt.invalid_candidate_length,
                    "validation_issues": attempt.validation_issues_json,
                }
                for attempt in attempts
            ],
            "aggregate_usage": {
                "usage_status": usage_status,
                "attempt_count": len(attempts),
                **aggregate,
            },
        }

    def backup(self, destination: Path) -> Path:
        destination = destination.expanduser().resolve()
        if destination == self.settings.database_path.resolve():
            raise ValueError("backup destination must differ from the live database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.settings.database_path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def list_research_chains(
        self,
        *,
        instrument: str | None = None,
    ) -> tuple[ResearchChain, ...]:
        stmt = select(ResearchChainRecord)
        if instrument is not None:
            stmt = stmt.where(ResearchChainRecord.instrument == instrument)
        stmt = stmt.order_by(
            ResearchChainRecord.is_primary.desc(),
            ResearchChainRecord.created_at,
        )
        with self.sessions() as session:
            records = tuple(session.scalars(stmt))
            return tuple(self._research_chain(session, record) for record in records)

    def get_research_chain(self, chain_id: str) -> ResearchChain:
        with self.sessions() as session:
            record = session.get(ResearchChainRecord, chain_id)
            if record is None:
                raise ResearchChainNotFoundError(chain_id)
            return self._research_chain(session, record)

    def get_research_revision(self, revision_id: str) -> ResearchRevision:
        with self.sessions() as session:
            record = session.get(ResearchRevisionRecord, revision_id)
            if record is None:
                raise ResearchRevisionNotFoundError(revision_id)
            return self._research_revision(record)

    @staticmethod
    def _create_initial_revision(
        session: Session,
        *,
        record: RunRecord,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> str:
        existing = session.scalar(
            select(ResearchRevisionRecord).where(
                ResearchRevisionRecord.producing_run_id == record.id
            )
        )
        if existing is not None:
            return existing.id
        request = AnalysisRequest.model_validate(record.request_json)
        has_primary = session.scalar(
            select(ResearchChainRecord.id).where(
                ResearchChainRecord.instrument == request.ticker,
                ResearchChainRecord.is_primary.is_(True),
            )
        )
        chain_id = str(uuid4())
        revision_id = str(uuid4())
        chain = ResearchChainRecord(
            id=chain_id,
            instrument=request.ticker,
            is_primary=has_primary is None,
            current_revision_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(chain)
        session.flush()
        session.add(
            RunRepository._revision_record(
                revision_id=revision_id,
                chain_id=chain_id,
                sequence=1,
                predecessor_revision_id=None,
                producing_run_id=record.id,
                draft=draft,
                metrics=metrics,
                created_at=created_at,
            )
        )
        chain.current_revision_id = revision_id
        return revision_id

    @staticmethod
    def _advance_research_chain(
        session: Session,
        *,
        record: RunRecord,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> str:
        chain = session.get(ResearchChainRecord, record.research_chain_id)
        baseline = session.get(ResearchRevisionRecord, record.baseline_revision_id)
        if (
            chain is None
            or baseline is None
            or baseline.chain_id != chain.id
            or chain.current_revision_id != baseline.id
        ):
            raise InvalidResearchBaselineError(
                "Eligible Baseline is no longer the current Research Chain head"
            )
        request = AnalysisRequest.model_validate(record.request_json)
        if request.ticker != chain.instrument or draft.current_state.instrument != chain.instrument:
            raise InvalidResearchBaselineError(
                "completed update Instrument does not match the Research Chain"
            )
        if draft.cutoff <= baseline.cutoff:
            raise InvalidResearchBaselineError(
                "completed update cutoff must be strictly later than the Eligible Baseline"
            )
        revision_id = str(uuid4())
        session.add(
            RunRepository._revision_record(
                revision_id=revision_id,
                chain_id=chain.id,
                sequence=baseline.sequence + 1,
                predecessor_revision_id=baseline.id,
                producing_run_id=record.id,
                draft=draft,
                metrics=metrics,
                created_at=created_at,
            )
        )
        chain.current_revision_id = revision_id
        chain.updated_at = created_at
        return revision_id

    @staticmethod
    def _revision_record(
        *,
        revision_id: str,
        chain_id: str,
        sequence: int,
        predecessor_revision_id: str | None,
        producing_run_id: str,
        draft: ResearchRevisionDraft,
        metrics: RunMetrics,
        created_at: datetime,
    ) -> ResearchRevisionRecord:
        return ResearchRevisionRecord(
            id=revision_id,
            chain_id=chain_id,
            sequence=sequence,
            predecessor_revision_id=predecessor_revision_id,
            producing_run_id=producing_run_id,
            cutoff=draft.cutoff,
            role=draft.role.value,
            execution_strategy=draft.execution_strategy.value,
            legacy_outcome=(
                "material_change"
                if draft.change_conclusion is None
                else "coverage_incomplete"
                if draft.change_conclusion is ResearchChangeConclusion.INDETERMINATE
                else draft.change_conclusion.value
            ),
            change_conclusion=(
                draft.change_conclusion.value
                if draft.change_conclusion is not None
                else None
            ),
            indeterminate_reason=(
                draft.indeterminate_reason.value
                if draft.indeterminate_reason is not None
                else None
            ),
            language=draft.current_state.language,
            current_state_json=draft.current_state.model_dump(mode="json"),
            delta_json=draft.delta.model_dump(mode="json"),
            coverage_json=draft.coverage.model_dump(mode="json"),
            update_summary_json=draft.update_summary.model_dump(mode="json"),
            evidence_snapshot_json=draft.evidence_snapshot.model_dump(mode="json"),
            research_update_audit_json=(
                draft.research_update_audit.model_dump(mode="json")
                if draft.research_update_audit is not None
                else None
            ),
            metrics_json=metrics.model_dump(mode="json"),
            created_at=created_at,
        )

    def _research_chain(
        self,
        session: Session,
        record: ResearchChainRecord,
    ) -> ResearchChain:
        revisions = tuple(
            self._research_revision(item)
            for item in session.scalars(
                select(ResearchRevisionRecord)
                .where(ResearchRevisionRecord.chain_id == record.id)
                .order_by(ResearchRevisionRecord.sequence)
            )
        )
        current = next(
            (item for item in revisions if item.id == record.current_revision_id),
            None,
        )
        if current is None:
            raise ValueError(f"Research Chain {record.id} has no current Revision")
        evaluation = evaluate_next_update_policy(
            current,
            instrument=record.instrument,
            mode=self.settings.research_update_mode,
        )
        return ResearchChain(
            id=record.id,
            instrument=record.instrument,
            is_primary=record.is_primary,
            current_revision_id=current.id,
            current_revision=current,
            revisions=revisions,
            next_update_policy=evaluation.policy,
            next_update_reason=evaluation.reason,
            created_at=_aware(record.created_at),
            updated_at=_aware(record.updated_at),
        )

    @staticmethod
    def _research_revision(record: ResearchRevisionRecord) -> ResearchRevision:
        return ResearchRevision(
            id=record.id,
            chain_id=record.chain_id,
            sequence=record.sequence,
            predecessor_revision_id=record.predecessor_revision_id,
            producing_run_id=record.producing_run_id,
            cutoff=record.cutoff,
            role=ResearchRevisionRole(record.role),
            execution_strategy=ResearchExecutionStrategy(record.execution_strategy),
            change_conclusion=(
                ResearchChangeConclusion(record.change_conclusion)
                if record.change_conclusion is not None
                else None
            ),
            indeterminate_reason=(
                IndeterminateReason(record.indeterminate_reason)
                if record.indeterminate_reason is not None
                else None
            ),
            current_state=CurrentResearchState.model_validate(record.current_state_json),
            delta=RevisionDelta.model_validate(record.delta_json),
            coverage=CoverageAttestation.model_validate(record.coverage_json),
            update_summary=UpdateSummary.model_validate(record.update_summary_json),
            evidence_snapshot=EffectiveEvidenceSnapshot.model_validate(
                record.evidence_snapshot_json
            ),
            research_update_audit=(
                ResearchUpdateAudit.model_validate(record.research_update_audit_json)
                if record.research_update_audit_json is not None
                else None
            ),
            metrics=RunMetrics.model_validate(record.metrics_json),
            created_at=_aware(record.created_at),
        )

    @staticmethod
    def market_bucket(ticker: str) -> str | None:
        try:
            return str(market_timezone(ticker))
        except ValueError:
            return None

    @staticmethod
    def _attempt(session: Session, record: RunRecord) -> RunAttemptRecord:
        return session.scalar(
            select(RunAttemptRecord).where(
                RunAttemptRecord.run_id == record.id,
                RunAttemptRecord.attempt == record.current_attempt,
            )
        )

    @staticmethod
    def _merge_metrics(
        record: RunRecord,
        attempt: RunAttemptRecord,
        segment: RunMetrics | None,
    ) -> RunMetrics:
        if segment is None:
            return RunMetrics.model_validate(record.metrics_json or {})
        attempt_metrics = merge_run_metrics(
            RunMetrics.model_validate(attempt.metrics_json or {}),
            segment,
        )
        aggregate = merge_run_metrics(
            RunMetrics.model_validate(record.metrics_json or {}),
            segment,
        )
        attempt.metrics_json = attempt_metrics.model_dump(mode="json")
        record.metrics_json = aggregate.model_dump(mode="json")
        return aggregate

    @staticmethod
    def _view(record: RunRecord) -> RunView:
        return RunView(
            id=record.id,
            source_run_id=record.source_run_id,
            instrument_name=record.instrument_name,
            instrument_local_name=record.instrument_local_name,
            research_chain_requested=record.research_chain_requested,
            update_intent_id=record.update_intent_id,
            research_chain_id=record.research_chain_id,
            baseline_revision_id=record.baseline_revision_id,
            research_execution_strategy=record.research_execution_strategy,
            research_update_audit=(
                ResearchUpdateAudit.model_validate(record.research_update_audit_json)
                if record.research_update_audit_json is not None
                else None
            ),
            status=RunStatus(record.status),
            request=AnalysisRequest.model_validate(record.request_json),
            config_snapshot=record.config_json,
            attempt=record.current_attempt,
            cancel_requested=record.cancel_requested,
            error_code=record.error_code,
            error_message=record.error_message,
            metrics=RunMetrics.model_validate(record.metrics_json or {}),
            created_at=_aware(record.created_at),
            started_at=_aware(record.started_at),
            finished_at=_aware(record.finished_at),
            trashed_at=_aware(record.trashed_at),
            updated_at=_aware(record.updated_at),
        )

    @classmethod
    def _summary(
        cls,
        record: RunRecord,
        rating: str | None,
    ) -> RunSummaryView:
        return RunSummaryView(
            **cls._view(record).model_dump(),
            research_rating=ResearchRating(rating) if rating else None,
        )
