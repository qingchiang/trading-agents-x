"""Internal execution operations on a shared repository session."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from tradingagents.domain.common import (
    RunStatus,
)
from tradingagents.domain.errors import (
    IncrementalRequestConflictError,
)
from tradingagents.domain.runs import (
    AnalysisRequest,
    RunMetrics,
    RunView,
)
from tradingagents.persistence._repository_common import (
    _TERMINAL_STATUSES,
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunNotFoundError,
    _sanitize_payload,
    _sanitize_text,
    _utc_naive,
)
from tradingagents.persistence.models import (
    PrimaryResearchCycleRecord,
    RunAttemptRecord,
    RunRecord,
)
from tradingagents.version import __version__


class ExecutionOperations:
    def replay_submission(
        self, idempotency_key: str, identity: dict[str, Any]
    ) -> RunView | None:
        from tradingagents.persistence.submissions import matches_submission

        with self.sessions() as session:
            existing = session.scalar(
                select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
            )
            if existing is None:
                return None
            if not matches_submission(existing, identity):
                raise IdempotencyConflictError(
                    "idempotency key was already used for a different request"
                )
            return self._view_for_session(session, existing)

    def create_run(
        self,
        request: AnalysisRequest,
        config_snapshot: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        source_run_id: str | None = None,
        research_schema_version: str | None = None,
        information_cutoff_at: datetime | None = None,
        method_snapshot: dict[str, Any] | None = None,
        research_kind: str | None = None,
        full_baseline_run_id: str | None = None,
        incremental_input_fingerprint: str | None = None,
        submission_identity: dict[str, Any] | None = None,
    ) -> tuple[RunView, bool]:
        if not isinstance(request, AnalysisRequest):
            raise TypeError("new Runs require an AnalysisRequest creation contract")
        now = _utc_naive()
        request_json = request.model_dump(mode="json")
        from tradingagents.persistence.submissions import matches_submission

        def matches(existing: RunRecord) -> bool:
            if submission_identity is not None:
                return matches_submission(existing, submission_identity)
            return existing.request_json == request_json and existing.source_run_id == source_run_id

        try:
            with self.sessions.begin() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                if idempotency_key:
                    existing = session.scalar(
                        select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                    )
                    if existing is not None:
                        if not matches(existing):
                            raise IdempotencyConflictError(
                                "idempotency key was already used for a different request"
                            )
                        return self._view_for_session(session, existing), False
                if research_kind == "incremental":
                    if full_baseline_run_id is None or incremental_input_fingerprint is None:
                        raise ValueError(
                            "Incremental Research requires immutable baseline and fingerprint"
                        )
                    existing_slot = self._active_incremental_slot(
                        session,
                        full_baseline_run_id,
                        request.analysis_date,
                    )
                    if existing_slot is not None:
                        if (
                            existing_slot.incremental_input_fingerprint
                            == incremental_input_fingerprint
                        ):
                            return self._view_for_session(session, existing_slot), False
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies this Cycle and cutoff."
                        )
                if (
                    research_kind == "full"
                    and request.make_primary is None
                    and session.get(PrimaryResearchCycleRecord, request.ticker) is not None
                ):
                    raise ValueError("later Full Research requires an explicit make_primary choice")
                if source_run_id is not None:
                    source = session.get(RunRecord, source_run_id)
                    if source is None:
                        raise RunNotFoundError(source_run_id)
                    if source.status not in _TERMINAL_STATUSES:
                        raise InvalidRunTransitionError(
                            "source run must be terminal before it can be "
                            "used as a research template"
                        )
                from tradingagents.persistence.connections import validate_run_connections

                validate_run_connections(session, config_snapshot)
                run_id = str(uuid4())
                record = RunRecord(
                    id=run_id,
                    source_run_id=source_run_id,
                    idempotency_key=idempotency_key,
                    status=RunStatus.QUEUED.value,
                    request_json=request_json,
                    submission_json=submission_identity,
                    config_json=_sanitize_payload(config_snapshot),
                    research_schema_version=research_schema_version,
                    information_cutoff_at=(
                        information_cutoff_at.astimezone(UTC).replace(tzinfo=None)
                        if information_cutoff_at and information_cutoff_at.tzinfo
                        else information_cutoff_at
                    ),
                    method_snapshot_json=_sanitize_payload(method_snapshot)
                    if method_snapshot is not None
                    else None,
                    research_kind=research_kind,
                    full_baseline_run_id=full_baseline_run_id,
                    incremental_cutoff=(
                        request.analysis_date if research_kind == "incremental" else None
                    ),
                    incremental_input_fingerprint=incremental_input_fingerprint,
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
            if research_kind == "incremental":
                with self.sessions() as session:
                    existing_slot = self._active_incremental_slot(
                        session,
                        full_baseline_run_id,
                        request.analysis_date,
                    )
                    if existing_slot is not None:
                        if (
                            existing_slot.incremental_input_fingerprint
                            == incremental_input_fingerprint
                        ):
                            return self._view_for_session(session, existing_slot), False
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies this Cycle and cutoff."
                        ) from exc
            if idempotency_key is None:
                raise
            with self.sessions() as session:
                existing = session.scalar(
                    select(RunRecord).where(RunRecord.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise
                if not matches(existing):
                    raise IdempotencyConflictError(
                        "idempotency key was already used for a different request"
                    ) from exc
                return self._view_for_session(session, existing), False
        return self.get_run(run_id), True

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
        slot_identity: tuple[str, Any] | None = None
        fingerprint: str | None = None
        try:
            with self.sessions.begin() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                record = session.get(RunRecord, run_id)
                if record is None:
                    raise RunNotFoundError(run_id)
                self._require_retryable(record)
                from tradingagents.persistence.connections import validate_run_connections

                validate_run_connections(session, record.config_json, retry=True)
                if (
                    record.research_kind == "incremental"
                    and record.full_baseline_run_id is not None
                    and record.incremental_cutoff is not None
                ):
                    slot_identity = (
                        record.full_baseline_run_id,
                        record.incremental_cutoff,
                    )
                    fingerprint = record.incremental_input_fingerprint
                    existing_slot = self._active_incremental_slot(
                        session,
                        *slot_identity,
                    )
                    if existing_slot is not None:
                        if existing_slot.incremental_input_fingerprint == fingerprint:
                            return self._view_for_session(session, existing_slot)
                        raise IncrementalRequestConflictError(
                            "An active Incremental Research Run already occupies "
                            "this Cycle and cutoff."
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
        except IntegrityError as exc:
            if slot_identity is None:
                raise
            with self.sessions() as session:
                existing_slot = self._active_incremental_slot(
                    session,
                    *slot_identity,
                )
                if existing_slot is None:
                    raise
                if existing_slot.incremental_input_fingerprint == fingerprint:
                    return self._view_for_session(session, existing_slot)
            raise IncrementalRequestConflictError(
                "An active Incremental Research Run already occupies this Cycle and cutoff."
            ) from exc
        return self.get_run(run_id)

    def require_retryable(self, run_id: str) -> RunView:
        """Check the target lifecycle before any retry admission work."""
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            self._require_retryable(record)
            return self._view_for_session(session, record)

    def checkpoint_thread(self, run_id: str) -> str:
        with self.sessions() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            attempt = self._attempt(session, record)
            return attempt.checkpoint_thread_id

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
