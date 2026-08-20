"""Transactional repository for runs, events, reports, and research memory."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
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
    ResearchWarning,
    RiskReview,
    RunAttemptView,
    RunEvent,
    RunMetrics,
    RunPage,
    RunRequestSnapshot,
    RunStatus,
    RunSummaryView,
    RunTrashState,
    RunView,
    StructuredRecoveryNotice,
)
from .database import (
    Base,
    DecisionRecord,
    OutcomeRecord,
    ReflectionRecord,
    RunArtifactRecord,
    RunAttemptRecord,
    RunEventRecord,
    RunEvidenceRecord,
    RunRecord,
    create_sqlite_engine,
)
from .metrics import merge_run_metrics
from .recoveries import rebuild_structured_recoveries
from .reporting import order_reports
from .settings import AppSettings

_SECRET_RE = re.compile(
    r"(?i)(api[-_ ]?key|authorization|bearer|password|secret|token)(\s*[:=]\s*)(\S+)"
)
_TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}


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
    redacted = _SECRET_RE.sub(r"\1\2[REDACTED]", str(value))
    return redacted[:limit]


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


class RunRepository:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        settings.prepare_filesystem()
        self.engine = create_sqlite_engine(
            settings.database_path,
            busy_timeout_ms=settings.busy_timeout_ms,
        )
        self.sessions = sessionmaker(
            self.engine, expire_on_commit=False, class_=Session
        )

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
    ) -> tuple[RunView, bool]:
        if not isinstance(request, AnalysisRequest):
            raise TypeError(
                "new Runs require an AnalysisRequest creation contract"
            )
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        try:
            with self.sessions.begin() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = session.scalar(
                        select(RunRecord).where(
                            RunRecord.idempotency_key == idempotency_key
                        )
                    )
                    if existing is not None:
                        if (
                            existing.request_json != request_json
                            or existing.source_run_id != source_run_id
                        ):
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a "
                                "different request"
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
                    select(RunRecord).where(
                        RunRecord.idempotency_key == idempotency_key
                    )
                )
                if existing is None:
                    raise
                if (
                    existing.request_json != request_json
                    or existing.source_run_id != source_run_id
                ):
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view(existing), False
        return self.get_run(run_id), True

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
                    func.lower(
                        func.coalesce(RunRecord.instrument_name, "")
                    ).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(
                        func.coalesce(RunRecord.instrument_local_name, "")
                    ).contains(
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
                    self._summary(record, rating)
                    for record, rating in session.execute(stmt)
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
                for record in session.scalars(
                    select(RunRecord).where(RunRecord.id.in_(run_ids))
                )
            }
            missing = [run_id for run_id in run_ids if run_id not in records]
            if missing:
                raise RunNotFoundError(", ".join(missing))
            invalid = [
                record.id
                for record in records.values()
                if record.trashed_at is None
                and record.status not in _TERMINAL_STATUSES
            ]
            if invalid:
                raise InvalidRunTransitionError(
                    "only terminal runs can be trashed: "
                    + ", ".join(invalid)
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
                for record in session.scalars(
                    select(RunRecord).where(RunRecord.id.in_(run_ids))
                )
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
            if isinstance(instrument_local_name, str)
            and instrument_local_name.strip()
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
        asset_type = func.coalesce(
            func.json_extract(RunRecord.request_json, "$.asset_type"),
            "stock",
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
                asset_type == "stock",
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

    def active_run_counts(self) -> dict[str, int]:
        """Count current stock Runs by lifecycle status for health reporting."""
        asset_type = func.coalesce(
            func.json_extract(RunRecord.request_json, "$.asset_type"),
            "stock",
        )
        stmt = (
            select(RunRecord.status, func.count())
            .where(
                RunRecord.trashed_at.is_(None),
                asset_type == "stock",
            )
            .group_by(RunRecord.status)
        )
        with self.sessions() as session:
            return {
                str(status): int(count)
                for status, count in session.execute(stmt)
            }

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
            cutoff = cutoff.astimezone(UTC).replace(tzinfo=None)
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
            candidate = connection.execute(
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
                    )
                )
                .order_by(RunRecord.created_at)
                .limit(1)
            ).mappings().first()
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

    def claim_run(
        self, run_id: str, worker_id: str, lease_seconds: int
    ) -> RunView:
        """Claim a specific queued run for the synchronous Python API."""
        now = _utc_naive()
        expires = now + timedelta(seconds=lease_seconds)
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.trashed_at is not None:
                raise InvalidRunTransitionError(
                    f"run {run_id} is trashed"
                )
            if record.status != RunStatus.QUEUED.value:
                raise InvalidRunTransitionError(
                    f"run {run_id} is {record.status}, expected queued"
                )
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
            if (
                record.status != RunStatus.RUNNING.value
                or record.lease_owner != worker_id
            ):
                raise InvalidRunTransitionError(
                    f"run {run_id} is not claimed by {worker_id}"
                )
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
                raise InvalidRunTransitionError(
                    f"run {run_id} is trashed"
                )
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
            value = session.scalar(
                select(RunRecord.cancel_requested).where(RunRecord.id == run_id)
            )
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
                raise InvalidRunTransitionError(
                    f"run {run_id} is trashed"
                )
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
            sequence = (
                connection.execute(
                    select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1)
                    .where(RunEventRecord.run_id == run_id)
                ).scalar_one()
            )
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
                    raise ArtifactConflictError(
                        "artifact identity replayed with different content"
                    )
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
                        item.model_dump(mode="json")
                        for item in draft.generation_observations
                    ],
                    content_type=draft.content_type,
                    content_json=draft.content.model_dump(mode="json"),
                    content_hash=draft.content_hash,
                    created_at=now,
                )
            )
            sequence = connection.execute(
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1)
                .where(RunEventRecord.run_id == run_id)
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
                    item.model_dump(mode="json")
                    for item in draft.generation_observations
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
                connection.execute(
                    select(table).where(table.c.run_id == run_id)
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["digest"] != digest:
                    connection.rollback()
                    raise EvidenceConflictError(
                        "evidence seal replayed with a different digest"
                    )
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
                select(func.coalesce(func.max(RunEventRecord.sequence), 0) + 1)
                .where(RunEventRecord.run_id == run_id)
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
                select(func.count())
                .select_from(RunRecord)
                .where(RunRecord.id == run_id)
            )
            if not run_exists:
                raise RunNotFoundError(run_id)
            record = (
                connection.execute(
                    select(RunEvidenceRecord.__table__).where(
                        RunEvidenceRecord.run_id == run_id
                    )
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
    ) -> RunMetrics:
        """Persist a terminal result without creating legacy review state."""
        now = _utc_naive()
        with self.sessions.begin() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            if record.status != RunStatus.RUNNING.value:
                raise InvalidRunTransitionError(record.status)
            sealed = session.get(RunEvidenceRecord, run_id)
            if sealed is None:
                raise EvidenceNotSealedError(run_id)
            if sealed.digest != evidence.digest:
                raise EvidenceConflictError(
                    "completed result does not match the sealed evidence"
                )
            if result.decision is not None:
                if result.decision.memory_refs:
                    raise ValueError(
                        "new research decisions cannot contain legacy memory refs"
                    )
                request = RunRequestSnapshot.model_validate(record.request_json)
                market = self.market_bucket(request.ticker)
                decision = DecisionRecord(
                    run_id=run_id,
                    ticker=request.ticker,
                    market=market,
                    asset_type=request.asset_type,
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
                if artifact.stage == "analyst"
                and isinstance(artifact.content, AnalystReport)
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
            DecisionNumericAuditAppendix.model_validate(
                decision_record.numeric_audit_json
            )
            if decision_record and decision_record.numeric_audit_json
            else None
        )
        evidence = (
            EvidenceBundle.model_validate(evidence_record.bundle_json)
            if evidence_record
            else None
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
                                    "decision.numeric_audit_"
                                    f"{decision.numeric_audit_status.value}"
                                ),
                                message=(
                                    _numeric_audit_warning_message(numeric_audit)
                                ),
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
                connection.execute(
                    select(RunRecord.id).where(RunRecord.id == run_id)
                ).first()
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
            raise ValueError(
                f"unsupported research artifact type: {record['content_type']}"
            )
        generation_method = ArtifactGenerationMethod(
            record["generation_method"]
        )
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
            due = due.astimezone(UTC).replace(tzinfo=None)
        stmt = (
            select(OutcomeRecord, DecisionRecord)
            .join(DecisionRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .where(
                OutcomeRecord.status == "pending",
                DecisionRecord.asset_type == "stock",
                RunRecord.trashed_at.is_(None),
                OutcomeRecord.next_check_at.is_not(None),
                OutcomeRecord.next_check_at <= due,
            )
            .order_by(OutcomeRecord.next_check_at, DecisionRecord.analysis_date)
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
                    "next_check_at": _aware(outcome.next_check_at),
                }
                for outcome, decision in session.execute(stmt)
            ]

    def pending_outcome_count(self) -> int:
        """Count active stock outcomes still scheduled for settlement."""
        stmt = (
            select(func.count())
            .select_from(OutcomeRecord)
            .join(DecisionRecord, OutcomeRecord.decision_id == DecisionRecord.id)
            .join(RunRecord, RunRecord.id == DecisionRecord.run_id)
            .where(
                OutcomeRecord.status == "pending",
                DecisionRecord.asset_type == "stock",
                RunRecord.trashed_at.is_(None),
            )
        )
        with self.sessions() as session:
            return int(session.scalar(stmt) or 0)

    def mark_outcome_checked(
        self,
        outcome_id: int,
        *,
        checked_at: datetime,
        next_check_at: datetime,
        error_message: str | None = None,
    ) -> None:
        if checked_at.tzinfo is not None:
            checked_at = checked_at.astimezone(UTC).replace(tzinfo=None)
        if next_check_at.tzinfo is not None:
            next_check_at = next_check_at.astimezone(UTC).replace(
                tzinfo=None
            )
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
                    DecisionRecord.asset_type == "stock",
                    RunRecord.trashed_at.is_(None),
                )
            )
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
        now = _utc_naive()
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
                    DecisionRecord.asset_type == "stock",
                    RunRecord.trashed_at.is_(None),
                )
            )
            if outcome is None or outcome.status != "pending":
                return
            outcome.status = "resolved"
            outcome.observation_start = observation_start
            outcome.observation_end = observation_end
            outcome.raw_return = raw_return
            outcome.alpha_return = alpha_return
            outcome.last_checked_at = now
            outcome.next_check_at = None
            outcome.resolved_at = now
            outcome.error_message = None
            session.add(
                ReflectionRecord(
                    outcome_id=outcome.id,
                    text=reflection,
                    created_at=now,
                )
            )

    def memory_context(
        self,
        ticker: str,
        asset_type: str,
        *,
        same_limit: int = 5,
        cross_limit: int = 3,
    ) -> MemoryContext:
        if asset_type.casefold() != "stock":
            return MemoryContext(
                instrument=ticker,
                market=None,
                items=(),
            )
        market = self.market_bucket(ticker, asset_type)
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
                DecisionRecord.asset_type == "stock",
                OutcomeRecord.status == "resolved",
                OutcomeRecord.holding_intervals >= 5,
                OutcomeRecord.raw_return.is_not(None),
                OutcomeRecord.alpha_return.is_not(None),
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
            reflection = reflection_record.text.strip()
            if not reflection:
                continue
            try:
                decision = ResearchDecision.model_validate(
                    decision_record.decision_json
                )
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
            if (
                decision_record.ticker.casefold() == ticker_key
                and len(same) < max(0, same_limit)
            ):
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

    def memory_entries(
        self,
        *,
        ticker: str | None = None,
        market: str | None = None,
        q: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(
                DecisionRecord,
                OutcomeRecord,
                ReflectionRecord,
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
            .order_by(
                DecisionRecord.created_at.desc(),
                DecisionRecord.id.desc(),
            )
            .where(
                RunRecord.trashed_at.is_(None),
                DecisionRecord.asset_type == "stock",
            )
            .limit(min(max(1, limit), 500))
        )
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
                    func.lower(
                        func.coalesce(RunRecord.instrument_name, "")
                    ).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(
                        func.coalesce(RunRecord.instrument_local_name, "")
                    ).contains(
                        query,
                        autoescape=True,
                    ),
                    func.lower(
                        func.coalesce(DecisionRecord.market, "")
                    ).contains(
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
                    func.lower(
                        func.coalesce(ReflectionRecord.text, "")
                    ).contains(
                        query,
                        autoescape=True,
                    ),
                )
            )
        if status:
            stmt = stmt.where(OutcomeRecord.status == status)
        with self.sessions() as session:
            return [
                {
                    "run_id": decision.run_id,
                    "ticker": decision.ticker,
                    "instrument_name": instrument_name,
                    "instrument_local_name": instrument_local_name,
                    "market": decision.market,
                    "asset_type": decision.asset_type,
                    "analysis_date": decision.analysis_date.isoformat(),
                    "profile": RunRequestSnapshot.model_validate(request_json).profile,
                    "decision": decision.decision_json,
                    "outcome": {
                        "status": outcome.status,
                        "benchmark": outcome.benchmark,
                        "observation_start": (
                            outcome.observation_start.isoformat()
                            if outcome.observation_start
                            else None
                        ),
                        "observation_end": (
                            outcome.observation_end.isoformat()
                            if outcome.observation_end
                            else None
                        ),
                        "holding_intervals": outcome.holding_intervals,
                        "raw_return": outcome.raw_return,
                        "alpha_return": outcome.alpha_return,
                    },
                    "reflection": reflection.text if reflection else None,
                }
                for (
                    decision,
                    outcome,
                    reflection,
                    instrument_name,
                    instrument_local_name,
                    request_json,
                ) in session.execute(stmt)
            ]

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

    @staticmethod
    def market_bucket(ticker: str, asset_type: str | None = None) -> str | None:
        del asset_type  # retained for callers reading legacy decision rows
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
            status=RunStatus(record.status),
            request=RunRequestSnapshot.model_validate(record.request_json),
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
